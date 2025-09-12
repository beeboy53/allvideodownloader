from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import yt_dlp
import tempfile
import os
import uuid
import time
import threading

app = FastAPI()

# ✅ Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restrict later to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Temporary directory for merged MP4s
DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), "video_downloader")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# File expiry tracker {filename: expiry_timestamp}
FILE_EXPIRY = {}


@app.get("/")
def home():
    return {"message": "Video Downloader API is running 🚀"}


@app.get("/download")
def download_video(
    url: str = Query(..., description="Video URL to download"),
    expiry: int = Query(3600, description="File expiry in seconds (default 3600s = 1 hour)"),
    request: Request = None
):
    """
    Download & merge best video + audio, return API link to final MP4.
    Files auto-delete after `expiry` seconds.
    """
    try:
        # Generate random file name
        file_id = str(uuid.uuid4())
        output_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")

        ydl_opts = {
            "quiet": True,
            "format": "bestvideo+bestaudio/best",  # ✅ Merge best video + audio
            "merge_output_format": "mp4",
            "outtmpl": output_path,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        # Save expiry timestamp
        FILE_EXPIRY[f"{file_id}.mp4"] = time.time() + expiry

        # Build absolute API file URL
        base_url = str(request.base_url).rstrip("/")
        file_url = f"{base_url}/files/{file_id}.mp4"

        return {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "download_url": file_url,
            "expires_in": expiry,
        }

    except Exception as e:
        return {"error": str(e)}


@app.get("/files/{filename}")
def serve_file(filename: str):
    """
    Serve downloaded video files if not expired
    """
    file_path = os.path.join(DOWNLOAD_DIR, filename)

    # Check expiry
    if filename in FILE_EXPIRY:
        if time.time() > FILE_EXPIRY[filename]:
            try:
                os.remove(file_path)
            except Exception:
                pass
            FILE_EXPIRY.pop(filename, None)
            return {"error": "File expired and removed"}

    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="video/mp4", filename=filename)

    return {"error": "File not found"}


# 🧹 Background cleaner: delete expired files
def cleanup_old_files():
    while True:
        now = time.time()
        expired = []
        for fname, expiry in FILE_EXPIRY.items():
            if now > expiry:
                fpath = os.path.join(DOWNLOAD_DIR, fname)
                if os.path.isfile(fpath):
                    try:
                        os.remove(fpath)
                        print(f"🧹 Deleted expired file: {fpath}")
                    except Exception as e:
                        print(f"⚠️ Cleanup error: {e}")
                expired.append(fname)

        # Remove from tracker
        for fname in expired:
            FILE_EXPIRY.pop(fname, None)

        time.sleep(300)  # Run every 5 minutes


# Start cleaner in background
threading.Thread(target=cleanup_old_files, daemon=True).start()
