from fastapi import FastAPI, Query, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import requests
import subprocess
import os
import uuid
import logging
import time
from urllib.parse import urlencode

# --- Cookies setup (optional from Gist for TikTok/Instagram auth) ---
COOKIE_FILE_PATH = "/tmp/cookies.txt"
cookie_gist_url = os.getenv("COOKIE_GIST_URL")
if cookie_gist_url:
    try:
        response = requests.get(cookie_gist_url)
        response.raise_for_status()
        with open(COOKIE_FILE_PATH, "w") as f:
            f.write(response.text)
        logging.info("✅ Cookies loaded from Gist.")
    except Exception as e:
        logging.error(f"⚠️ Failed to load cookies: {e}")
        open(COOKIE_FILE_PATH, "a").close()
else:
    open(COOKIE_FILE_PATH, "a").close()

# --- Logging ---
logging.basicConfig(level=logging.INFO)

# --- FastAPI App ---
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restrict later for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Track temp files for cleanup
TEMP_DIR = "/tmp/saveclips"
os.makedirs(TEMP_DIR, exist_ok=True)
FILE_EXPIRY = {}

def cleanup_files(paths: list):
    for path in paths:
        try:
            if os.path.exists(path):
                os.remove(path)
                logging.info(f"🧹 Deleted file: {path}")
        except Exception as e:
            logging.error(f"Error cleaning {path}: {e}")


@app.get("/")
def home():
    return {"message": "SaveClips API is running 🚀"}


@app.get("/info")
async def get_video_info(request: Request, url: str):
    """Fetch video metadata + formats (audio/video)."""
    ydl_opts = {"quiet": True}
    info = None
    last_exception = None

    # Retry logic
    for attempt in range(3):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info:
                break
        except Exception as e:
            last_exception = e
            time.sleep(1)

    if not info:
        raise HTTPException(status_code=500, detail=f"Could not fetch video info. {last_exception}")

    try:
        all_formats = []

        # Detect if video+audio merging is possible
        video_only = [f for f in info.get("formats", []) if f.get("vcodec") != "none" and f.get("acodec") == "none" and f.get("url")]
        audio_only = [f for f in info.get("formats", []) if f.get("acodec") != "none" and f.get("vcodec") == "none" and f.get("url")]

        if video_only and audio_only:
            merge_url = str(request.base_url) + "merge_streams?" + urlencode({"url": url})
            all_formats.append({
                "quality": "Best Quality (Merged)",
                "ext": "mp4",
                "url": merge_url,
                "vcodec": "merged",
                "acodec": "merged",
                "height": max(v.get("height", 0) for v in video_only)
            })

        # Add direct formats
        for f in info.get("formats", []):
            if f.get("url"):
                all_formats.append({
                    "url": f.get("url"),
                    "ext": f.get("ext"),
                    "height": f.get("height"),
                    "acodec": f.get("acodec"),
                    "vcodec": f.get("vcodec"),
                    "quality": f.get("format_note") or (f.get("height") and f"{f.get('height')}p") or "Audio",
                })

        # Sort by quality
        sorted_formats = sorted(all_formats, key=lambda x: x.get("height") or 0, reverse=True)

        return {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "formats": sorted_formats
        }
    except Exception as e:
        logging.error(f"Processing error: {e}")
        raise HTTPException(status_code=500, detail="Failed to process formats.")


@app.get("/merge_streams")
async def merge_streams(url: str, background_tasks: BackgroundTasks, request: Request):
    """Download best video+audio, merge, return JSON with download URL."""
    try:
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(url, download=False)

        # Pick best video + audio
        best_video = max((f for f in info["formats"] if f.get("vcodec") != "none" and f.get("acodec") == "none"), key=lambda x: x.get("height", 0))
        best_audio = max((f for f in info["formats"] if f.get("acodec") != "none" and f.get("vcodec") == "none"), key=lambda x: x.get("abr", 0))

        # Generate temp file paths
        request_id = str(uuid.uuid4())
        video_path = os.path.join(TEMP_DIR, f"{request_id}_v.mp4")
        audio_path = os.path.join(TEMP_DIR, f"{request_id}_a.m4a")
        output_path = os.path.join(TEMP_DIR, f"{request_id}_o.mp4")

        # Download video
        with requests.get(best_video["url"], stream=True) as r:
            r.raise_for_status()
            with open(video_path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)

        # Download audio
        with requests.get(best_audio["url"], stream=True) as r:
            r.raise_for_status()
            with open(audio_path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)

        # Merge with ffmpeg
        ffmpeg_cmd = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path, "-c", "copy", output_path]
        process = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if process.returncode != 0:
            raise HTTPException(status_code=500, detail="FFmpeg merge failed.")

        # Schedule cleanup
        background_tasks.add_task(cleanup_files, [video_path, audio_path, output_path])

        # Serve via /files/
        FILE_EXPIRY[os.path.basename(output_path)] = time.time() + 3600
        file_url = str(request.base_url) + "files/" + os.path.basename(output_path)

        return {
            "status": "ok",
            "title": info.get("title"),
            "download_url": file_url,
            "expires_in": 3600
        }
    except Exception as e:
        logging.error(f"Merge error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/files/{filename}")
async def serve_file(filename: str):
    """Serve merged file if not expired."""
    file_path = os.path.join(TEMP_DIR, filename)

    # Expiry check
    expiry = FILE_EXPIRY.get(filename)
    if expiry and time.time() > expiry:
        if os.path.exists(file_path):
            os.remove(file_path)
        FILE_EXPIRY.pop(filename, None)
        raise HTTPException(status_code=410, detail="File expired")

    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="video/mp4", filename=filename)

    raise HTTPException(status_code=404, detail="File not found")
