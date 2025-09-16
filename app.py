import os
import time
import logging
from pathlib import Path

import yt_dlp
import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# --- Configuration & Setup ---
logging.basicConfig(level=logging.INFO)
app = FastAPI()

TEMP_DIR = Path("/tmp")
COOKIE_FILE_PATH = TEMP_DIR / "cookies.txt"
COOKIE_GIST_URL = os.getenv("COOKIE_GIST_URL")


# --- Helper Function ---
def download_cookies():
    """Download cookies.txt from COOKIE_GIST_URL and save it locally."""
    if not COOKIE_GIST_URL:
        logging.warning("⚠️ COOKIE_GIST_URL is not set.")
        return False

    try:
        response = requests.get(COOKIE_GIST_URL, timeout=10)
        response.raise_for_status()
        COOKIE_FILE_PATH.write_text(response.text)
        logging.info("✅ Cookies refreshed successfully from Gist.")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Failed to refresh cookies: {e}")
        return False


# --- App Events ---
@app.on_event("startup")
def startup_event():
    if not download_cookies():
        logging.warning("⚠️ Proceeding without cookies. Instagram may not work.")


# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)


# --- API Endpoints ---
@app.get("/")
def home():
    return {"message": "SaveClips API is running 🚀"}


@app.get("/refresh_cookies")
def refresh_cookies():
    """Manually refresh cookies from Gist without restarting the server."""
    success = download_cookies()
    if success:
        return {"message": "✅ Cookies refreshed successfully."}
    return {"error": "❌ Failed to refresh cookies. Check logs."}


@app.get("/info")
async def get_video_info(request: Request, url: str):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'format': 'bestvideo+bestaudio/best'
    }
    if COOKIE_FILE_PATH.exists() and COOKIE_FILE_PATH.stat().st_size > 0:
        ydl_opts['cookiefile'] = str(COOKIE_FILE_PATH)

    info, last_exception = None, None
    for attempt in range(3):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info:
                break
        except Exception as e:
            last_exception = e
            logging.warning(f"Attempt {attempt + 1} failed for {url}. Retrying...")
            time.sleep(1)

    if not info:
        logging.error(f"❌ All retry attempts failed for {url}. Last error: {last_exception}")
        return {"error": f"Could not retrieve video information. Last error: {last_exception}"}

    try:
        all_formats = []
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
        sorted_formats = sorted(all_formats, key=lambda x: (x.get('height') or 0), reverse=True)
        return {"title": info.get("title"), "thumbnail": info.get("thumbnail"), "formats": sorted_formats}
    except Exception as e:
        logging.error(f"⚠️ Error processing formats for URL {url}: {e}")
        return {"error": "Successfully fetched video, but failed to process the download formats."}


@app.get("/instagram_info")
async def get_instagram_info(request: Request, url: str):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'ignoreerrors': True,
    }

    # ✅ Check if cookies exist
    if COOKIE_FILE_PATH.exists() and COOKIE_FILE_PATH.stat().st_size > 0:
        ydl_opts['cookiefile'] = str(COOKIE_FILE_PATH)
    else:
        return {"error": "Instagram cookies are missing. Please upload a valid cookies.txt file."}

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # ✅ If yt-dlp gave us nothing, cookies are likely expired
        if not info:
            return {"error": "Instagram cookies may be expired or invalid. Please refresh cookies.txt."}

        media_items = []

        # Handle carousel posts
        if 'entries' in info and info['entries']:
            for entry in info['entries']:
                if not entry:
                    continue
                if entry.get('url'):
                    if entry.get('vcodec') and entry['vcodec'] != 'none':
                        media_items.append({
                            "type": "video",
                            "thumbnail": entry.get('thumbnail'),
                            "url": entry.get('url')
                        })
                    else:
                        media_items.append({
                            "type": "image",
                            "thumbnail": entry.get('thumbnail'),
                            "url": entry.get('url')
                        })
        else:
            # Handle single post (image or video)
            if info.get('url'):
                if info.get('vcodec') and info['vcodec'] != 'none':
                    media_items.append({
                        "type": "video",
                        "thumbnail": info.get('thumbnail'),
                        "url": info.get('url')
                    })
                else:
                    media_items.append({
                        "type": "image",
                        "thumbnail": info.get('thumbnail'),
                        "url": info.get('url')
                    })

        if not media_items:
            return {"error": "No downloadable media were found. (Tip: Refresh cookies.txt if this is a video/reel)"}

        return {
            "title": info.get('title'),
            "uploader": info.get('uploader'),
            "media": media_items
        }

    except Exception as e:
        logging.error(f"Error processing Instagram formats: {e}")
        return {"error": f"Unexpected error: {str(e)}"}
