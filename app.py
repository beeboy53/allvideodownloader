from fastapi import FastAPI, Query, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import requests
import subprocess
import os
import uuid
import logging
import time 
from urllib.parse import urlencode

# --- ✨ NEW: SECURELY HANDLE COOKIES FROM A GIST URL ---
# Define the path for our temporary cookie file
COOKIE_FILE_PATH = "/tmp/cookies.txt"

# Read the secret Gist URL from the environment variable set in Railway
cookie_gist_url = os.getenv("COOKIE_GIST_URL")

# If the URL exists, download its content and write it to the temporary file
if cookie_gist_url:
    try:
        response = requests.get(cookie_gist_url)
        response.raise_for_status() # Raise an exception for bad status codes
        with open(COOKIE_FILE_PATH, "w") as f:
            f.write(response.text)
        logging.info("Successfully loaded cookies from Gist.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to download cookies from Gist: {e}")
        # Create an empty file so the app doesn't crash
        open(COOKIE_FILE_PATH, 'a').close()
else:
    # If the env var isn't set, create an empty file
    logging.warning("COOKIE_GIST_URL not set. Proceeding without cookies.")
    open(COOKIE_FILE_PATH, 'a').close()
# --- END OF NEW SECTION ---


logging.basicConfig(level=logging.INFO)
app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

def cleanup_files(paths: list):
    for path in paths:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            logging.error(f"Error cleaning up file {path}: {e}")

@app.get("/")
def home():
    return {"message": "Video Downloader API is running 🚀"}


     @app.get("/info")
async def get_video_info(request: Request, url: str):
    # This part remains the same
    ydl_opts = {
        'quiet': True,
        'cookiefile': COOKIE_FILE_PATH
    }
    
    info = None
    last_exception = None
    for attempt in range(3):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info:
                logging.info(f"Successfully fetched info for {url} on attempt {attempt + 1}")
                break 
        except Exception as e:
            last_exception = e
            logging.warning(f"Attempt {attempt + 1} failed for {url}. Retrying in 1 second...")
            time.sleep(1)

    # --- ✨ BACKEND FIX 1: IMPROVED ERROR RESPONSE ---
    if not info:
        logging.error(f"All retry attempts failed for {url}. Last error: {last_exception}")
        # Return a clean JSON error instead of raising HTTPException
        return {"error": "Could not retrieve video information. The link may be private, invalid, or contain extra text."}

    try:
        # This whole section of processing formats remains the same
        all_formats = []
        video_only = [f for f in info.get('formats', []) if f.get('vcodec') != 'none' and f.get('acodec') == 'none' and f.get('url')]
        audio_only = [f for f in info.get('formats', []) if f.get('acodec') != 'none' and f.get('vcodec') == 'none' and f.get('url')]

        if video_only and audio_only:
            query_params = urlencode({'url': url})
            merge_url = str(request.base_url) + "merge_streams?" + query_params
            all_formats.append({
                'quality': 'Best Quality (Merged)', 'ext': 'mp4', 'url': merge_url,
                'vcodec': 'merged', 'acodec': 'merged', 'height': max(v.get('height', 0) for v in video_only)
            })

        for f in info.get("formats", []):
            if f.get("url"):
                all_formats.append({
                    "url": f.get("url"), "ext": f.get("ext"), "height": f.get("height"),
                    "acodec": f.get("acodec"), "vcodec": f.get("vcodec"),
                    "quality": f.get("format_note") or (f.get("height") and f"{f.get('height')}p") or "Audio",
                })
        
        sorted_formats = sorted(all_formats, key=lambda x: x.get('height') or 0, reverse=True)

        return {
            "title": info.get("title"), "thumbnail": info.get("thumbnail"), "formats": sorted_formats
        }
    except Exception as e:
        logging.error(f"Error processing formats for URL {url}: {e}")
        # --- ✨ BACKEND FIX 2: IMPROVED ERROR RESPONSE ---
        return {"error": "Successfully fetched video, but failed to process the download formats."}


@app.get("/merge_streams")
async def merge_streams(url: str, background_tasks: BackgroundTasks):
    try:
        # This part remains the same
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
        
        best_video = max((f for f in info['formats'] if f.get('vcodec') != 'none' and f.get('acodec') == 'none'), key=lambda x: x.get('height', 0))
        best_audio = max((f for f in info['formats'] if f.get('acodec') != 'none' and f.get('vcodec') == 'none'), key=lambda x: x.get('abr', 0))

        request_id = str(uuid.uuid4())
        video_path, audio_path, output_path = f"/tmp/{request_id}_v.mp4", f"/tmp/{request_id}_a.m4a", f"/tmp/{request_id}_o.mp4"
        
        files_to_cleanup = [video_path, audio_path, output_path]
        background_tasks.add_task(cleanup_files, files_to_cleanup)

        with requests.get(best_video['url'], stream=True) as r:
            r.raise_for_status()
            with open(video_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
        with requests.get(best_audio['url'], stream=True) as r:
            r.raise_for_status()
            with open(audio_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
        
        ffmpeg_cmd = ['ffmpeg', '-i', video_path, '-i', audio_path, '-c', 'copy', output_path]
        process = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if process.returncode != 0:
            raise HTTPException(status_code=500, detail="Failed to merge files.")

        return FileResponse(path=output_path, media_type='video/mp4', filename=f"{info.get('title', 'video')}.mp4")
    except Exception as e:
        logging.error(f"Error in /merge_streams for URL {url}: {e}")
        # --- ✨ BACKEND FIX 3: IMPROVED ERROR RESPONSE ---
        # Note: This error will show to the user as a failed download, which is fine.
        # But for consistency, we could return a JSON object, though it's less critical here.
        raise HTTPException(status_code=500, detail=str(e))



