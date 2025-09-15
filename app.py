from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import subprocess
import os
import uuid
import time
import threading

app = FastAPI()

# Enable CORS (allow all for now)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Temp folder for merged files
DOWNLOAD_DIR = "/tmp/saveclips"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Track expiry {filename: expiry_timestamp}
FILE_EXPIRY = {}


@app.get("/")
def home():
    return {"message": "SaveClips API is running 🚀"}


@app.get("/info")
def get_info(url: str = Query(..., description="Video URL to fetch info")):
    """
    Return video title, thumbnail, and available formats.
    """
    try:
        ydl_opts = {"quiet": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = []
        for f in info.get("formats", []):
            if f.get("url"):
                formats.append({
                    "url": f["url"],
                    "ext": f.get("ext"),
                    "height": f.get("height"),
                    "vcodec": f.get("vcodec"),
                    "acodec": f.get("acodec"),
                    "quality": f.get("format_note") or (f.get("height") and f"{f['height']}p") or "Audio"
                })

        return {
            "title": info.get("title", "Untitled Video"),
            "thumbnail": info.get("thumbnail"),
            "formats": formats
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/merge_streams")
def merge_streams(
    url: str = Query(..., description="Video URL"),
    expiry: int = Query(3600, description="File expiry in seconds"),
    background_tasks: BackgroundTasks = None
):
    """
    Merge best video + best audio into MP4 using ffmpeg (re-encode).
    """
    try:
        # Extract info
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(url, download=False)

        # Pick best video-only & audio-only
        best_video = max(
            (f for f in info["formats"] if f.get("vcodec") != "none" and f.get("acodec") == "none"),
            key=lambda x: x.get("height") or 0
        )
        best_audio = max(
            (f for f in info["formats"] if f.get("acodec") != "none" and f.get("vcodec") == "none"),
            key=lambda x: x.get("abr") or 0
        )

        # Generate file path
        file_id = str(uuid.uuid4())
        output_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")

        # ffmpeg command (stream + re-encode)
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", best_video["url"],
            "-i", best_audio["url"],
            "-c:v", "libx264", "-c:a", "aac",
            "-movflags", "+faststart",
            output_path
        ]

        process = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=300)

        if process.returncode != 0:
            raise HTTPException(status_code=500, detail=f"FFmpeg failed: {process.stderr}")

        # Track expiry
        FILE_EXPIRY[f"{file_id}.mp4"] = time.time() + expiry

        return {
            "status": "ok",
            "title": info.get("title", "video"),
            "download_url": f"/files/{file_id}.mp4",
            "expires_in": expiry
        }

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="FFmpeg process timed out.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/files/{filename}")
def serve_file(filename: str):
    """
    Serve merged MP4 if not expired.
    """
    file_path = os.path.join(DOWNLOAD_DIR, filename)

    if filename in FILE_EXPIRY and time.time() > FILE_EXPIRY[filename]:
        try:
            os.remove(file_path)
        except:
            pass
        FILE_EXPIRY.pop(filename, None)
        raise HTTPException(status_code=410, detail="File expired")

    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="video/mp4", filename=filename)

    raise HTTPException(status_code=404, detail="File not found")


# Cleanup expired files in background
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
                    except:
                        pass
                expired.append(fname)
        for fname in expired:
            FILE_EXPIRY.pop(fname, None)
        time.sleep(300)


import threading
threading.Thread(target=cleanup_old_files, daemon=True).start()
