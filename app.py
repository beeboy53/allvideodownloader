import os
import time
import logging
from pathlib import Path

import yt_dlp
import requests
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware

# --- Configuration & Setup ---
logging.basicConfig(level=logging.INFO)
app = FastAPI()

TEMP_DIR = Path("/tmp")
COOKIE_FILE_PATH = TEMP_DIR / "cookies.txt"


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
    """Fetches video metadata and a list of available download formats."""
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
        for f in info.get("formats", []):
            if f.get("url"):
                all_formats.append({
                    "url": f.get("url"), "ext": f.get("ext"), "height": f.get("height"),
                    "acodec": f.get("acodec"), "vcodec": f.get("vcodec"),
                    "quality": f.get("format_note") or (f.get("height") and f"{f.get('height')}p") or "Audio",
                })
        
        sorted_formats = sorted(all_formats, key=lambda x: (x.get('height') or 0), reverse=True)

        return {"title": info.get("title"), "thumbnail": info.get("thumbnail"), "formats": sorted_formats}
    except Exception as e:
        logging.error(f"Error processing formats for URL {url}: {e}")
        return {"error": "Successfully fetched video, but failed to process the download formats."}

@app.get("/instagram_info")
async def get_instagram_info(request: Request, url: str):
    """
    Dedicated endpoint for Instagram that handles single posts and carousels (multi-image/video).
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
    }
    if COOKIE_FILE_PATH.exists() and COOKIE_FILE_PATH.stat().st_size > 0:
        ydl_opts['cookiefile'] = str(COOKIE_FILE_PATH)
    else:
        return {"error": "Server is not configured for Instagram downloads. Cookie file is missing."}

    info = None
    last_exception = None
    for attempt in range(3):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info:
                break
        except Exception as e:
            last_exception = e
            logging.warning(f"Instagram attempt {attempt + 1} failed. Retrying...")
            time.sleep(1)

    if not info:
        logging.error(f"All Instagram retry attempts failed. Last error: {last_exception}")
        return {"error": "Could not retrieve Instagram post. The link may be invalid or the post is private."}

    try:
        media_items = []
        
        # --- ✨ MODIFIED SECTION: This logic now filters for photos only ---
        if 'entries' in info:
            # It's a carousel, loop through each item
            for entry in info['entries']:
                # Only add the item if it's an image (does not have a video codec)
                if not entry.get('vcodec') or entry.get('vcodec') == 'none':
                    media_items.append({
                        "type": "image",
                        "thumbnail": entry.get('thumbnail'),
                        "url": entry.get('url')
                    })
        else:
            # It's a single post, check if it's an image
            if not info.get('vcodec') or info.get('vcodec') == 'none':
                media_items.append({
                    "type": "image",
                    "thumbnail": info.get('thumbnail'),
                    "url": info.get('url')
                })
        
        # If after filtering, no photos were found, return a specific error
        if not media_items:
            return {"error": "No downloadable photos were found in this post. This tool only supports downloading images."}
        # --- END OF MODIFIED SECTION ---
            
        return {
            "title": info.get('title'),
            "uploader": info.get('uploader'),
            "media": media_items
        }
    except Exception as e:
        logging.error(f"Error processing Instagram formats: {e}")
        return {"error": "Failed to process the Instagram media."}
