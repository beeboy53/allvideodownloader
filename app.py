import os
import uuid
import time
import logging
import subprocess
from urllib.parse import urlencode
from pathlib import Path
from datetime import datetime, timedelta

import requests
import yt_dlp
from fastapi import FastAPI, Query, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

# --- Configuration & Setup ---
logging.basicConfig(level=logging.INFO)
app = FastAPI()

TEMP_DIR = Path("/tmp")
COOKIE_FILE_PATH = TEMP_DIR / "cookies.txt"


# --- Helper Functions ---
def cleanup_files(paths: list):
    """Immediately cleans up a specific list of files."""
    for path in paths:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            logging.error(f"Error cleaning up file {path}: {e}")

def cleanup_old_files():
    """Cleans up any files in the temp directory older than 1 hour."""
    logging.info(f"Running startup cleanup of old files in {TEMP_DIR}...")
    now = datetime.now()
    cutoff = now - timedelta(hours=1)
    try:
        for path in TEMP_DIR.iterdir():
            if path.is_file():
                file_mod_time = datetime.fromtimestamp(path.stat().st_mtime)
                if file_mod_time < cutoff:
                    os.remove(path)
                    logging.info(f"Removed old temp file: {path.name}")
    except FileNotFoundError:
        logging.warning(f"Temp directory {TEMP_DIR} not found. Skipping cleanup.")
    except Exception as e:
        logging.error(f"Error during cleanup of old files: {e}")

def sanitize_filename(name: str) -> str:
    """Removes characters that are invalid in filenames."""
    if not name:
        return "video"
    return "".join(c for c in name if c.isalnum() or c in " ._-").rstrip()


# --- App Events ---
@app.on_event("startup")
def startup_event():
    """Tasks to run when the application starts."""
    cookie_gist_url = os.getenv("COOKIE_GIST_URL")
    if cookie_gist_url:
        try:
            response = requests.get(cookie_gist_url)
            response.raise_for_status()
            with open(COOKIE_FILE_PATH, "w") as f:
                f.write(response.text)
            logging.info("Successfully loaded cookies from Gist.")
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to download cookies from Gist: {e}")
    else:
        logging.warning("COOKIE_GIST_URL not set. Proceeding without cookies.")
    cleanup_old_files()


# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)


# --- API Endpoints ---
@app.get("/")
def home():
    return {"message": "SaveClips API is running 🚀"}

@app.get("/info")
async def get_video_info(request: Request, url: str):
    """Fetches video metadata and a list of all available download formats."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'format': 'bestvideo+bestaudio/best'
    }
    if COOKIE_FILE_PATH.exists() and COOKIE_FILE_PATH.stat().st_size > 0:
        ydl_opts['cookiefile'] = str(COOKIE_FILE_PATH)

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
            logging.warning(f"Attempt {attempt + 1} failed for {url}. Retrying...")
            time.sleep(1)

    if not info:
        logging.error(f"All retry attempts failed for {url}. Last error: {last_exception}")
        return {"error": "Could not retrieve video information. The link may be private, invalid, or the site may be unsupported."}

    try:
        all_formats = []
        
        # --- ✨ NEW: Smart Merge Logic ---
        # 1. Find all available pre-merged formats.
        pre_merged_formats = [
            f for f in info.get('formats', []) 
            if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('url')
        ]
        
        # 2. Check if a 'good enough' pre-merged file exists (e.g., 720p or higher MP4).
        good_pre_merged_exists = any(
            (f.get('height') or 0) >= 720 and f.get('ext') == 'mp4' for f in pre_merged_formats
        )

        # 3. If no good pre-merged file exists, THEN create the manual merge option.
        if not good_pre_merged_exists:
            logging.info("No high-quality pre-merged MP4 found. Checking if a manual merge is possible.")
            video_only = [f for f in info.get('formats', []) if f.get('vcodec') != 'none' and f.get('acodec') == 'none' and f.get('url')]
            audio_only = [f for f in info.get('formats', []) if f.get('acodec') != 'none' and f.get('vcodec') == 'none' and f.get('url')]

            if video_only and audio_only:
                logging.info("Separate streams found. Creating 'Best Quality (Merged)' option.")
                query_params = urlencode({'url': url})
                merge_url = str(request.base_url) + "merge_streams?" + query_params
                all_formats.append({
                    'quality': 'Best Quality (Merged)', 'ext': 'mp4', 'url': merge_url,
                    'vcodec': 'merged', 'acodec': 'merged', 'height': max((v.get('height') or 0 for v in video_only))
                })
        else:
            logging.info("Found a high-quality pre-merged file. Merge option will not be created.")
        # --- End of Smart Merge Logic ---

        # Add all originally available formats to the list
        for f in info.get("formats", []):
            if f.get("url"):
                all_formats.append({
                    "url": f.get("url"), "ext": f.get("ext"), "height": f.get("height"),
                    "acodec": f.get("acodec"), "vcodec": f.get("vcodec"),
                    "quality": f.get("format_note") or (f.get("height") and f"{f.get('height')}p") or "Audio",
                })
        
        # Remove duplicates based on URL, keeping the first occurrence (which would be our merged one if it exists)
        unique_formats = list({f['url']: f for f in all_formats}.values())
        
        # Sort by height (descending), putting our "merged" option first
        sorted_formats = sorted(unique_formats, key=lambda x: (x.get('height') or 0, x.get('vcodec') == 'merged'), reverse=True)

        return {"title": info.get("title"), "thumbnail": info.get("thumbnail"), "formats": sorted_formats}
    except Exception as e:
        logging.error(f"Error processing formats for URL {url}: {e}")
        return {"error": "Successfully fetched video, but failed to process the download formats."}

@app.get("/merge_streams")
async def merge_streams(url: str, background_tasks: BackgroundTasks):
    """Downloads, merges, and returns the final MP4 file."""
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'noplaylist': True}) as ydl:
            info = ydl.extract_info(url, download=False)
        
        best_video = max((f for f in info['formats'] if f.get('vcodec') != 'none' and f.get('acodec') == 'none'), key=lambda x: x.get('height', 0))
        best_audio = max((f for f in info['formats'] if f.get('acodec') != 'none' and f.get('vcodec') == 'none'), key=lambda x: x.get('abr', 0))

        request_id = str(uuid.uuid4())
        video_path = TEMP_DIR / f"{request_id}_v.mp4"
        audio_path = TEMP_DIR / f"{request_id}_a.m4a"
        output_path = TEMP_DIR / f"{request_id}_o.mp4"
        
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
        
        ffmpeg_cmd = ['ffmpeg', '-i', str(video_path), '-i', str(audio_path), '-c', 'copy', str(output_path)]
        process = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if process.returncode != 0:
            logging.error(f"FFmpeg Error: {process.stderr}")
            raise HTTPException(status_code=500, detail=f"Failed to merge files. FFmpeg error: {process.stderr}")

        safe_filename = sanitize_filename(info.get('title')) + ".mp4"
        
        return FileResponse(path=output_path, media_type='video/mp4', filename=safe_filename)

    except Exception as e:
        logging.error(f"Error in /merge_streams for URL {url}: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred during the merge process.")
