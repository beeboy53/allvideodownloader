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
import threading

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

# Directory for temporary merged files
DOWNLOAD_DIR = "/tmp/saveclips"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Track expiry times {filename: expiry_timestamp}
FILE_EXPIRY = {}

def cleanup_files():
    """Background job: remove expired files"""
    while True:
        now = time.time()
        expired = [f for f, exp in FILE_EXPIRY.items() if now > exp]
        for fname in expired:
            try:
                fpath = os.path.join(DOWNLOAD_DIR, fname)
                if os.path.isfile(fpath):
                    os.remove(fpath)
                    logging.info(f"🧹 Deleted expired file {fname}")
            except Exception as e:
                logging.error(f"Error deleting {fname}: {e}")
            FILE_EXPIRY.pop(fname, None)
        time.sleep(300)  # check every 5 mins

threading.Thread(target=cleanup_files, daemon=True).start()


@app.get("/")
def home():
    return {"message": "Video Downloader API is running 🚀"}


@app.get("/info")
async def get_video_info(request: Request, url: str):
    """Fetch metadata + formats"""
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)

        all_formats = []
        video_only = [f for f in info.get('formats', []) if f.get('vcodec') != 'none' and f.get('acodec') == 'none']
        audio_only = [f for f in info.get('formats', []) if f.get('acodec') != 'none' and f.get('vcodec') == 'none']

        # Add merged option if both exist
        if video_only and audio_only:
            merge_url = str(request.base_url) + "merge_streams?url=" + url
            all_formats.append({
                'quality': 'Best Quality (Merged)',
                'ext': 'mp4',
                'url': merge_url,
                'download_url': merge_url,
                'vcodec': 'merged',
                'acodec': 'merged',
                'height': max(v.get('height', 0) for v in video_only)
            })

        for f in info.get("formats", []):
            if f.get("url"):
                all_formats.append({
                    "url": f.get("url"),
                    "ext": f.get("ext"),
                    "height": f.get("height"),
                    "acodec": f.get("acodec"),
                    "vcodec": f.get("vcodec"),
                    "quality": f.get("format_note") or (f.get("height") and f"{f.get('height')}p") or "Audio",
                    "filesize": f.get("filesize"),
                    "filesize_approx": f.get("filesize_approx")
                })

        return {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "formats": sorted(all_formats, key=lambda x: x.get('height') or 0, reverse=True)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch video info: {str(e)}")


@app.get("/merge_streams")
async def merge_streams(url: str):
    """Download + merge best video/audio → return as forced file download"""
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)

        best_video = max((f for f in info['formats'] if f.get('vcodec') != 'none' and f.get('acodec') == 'none'),
                         key=lambda x: x.get('height', 0))
        best_audio = max((f for f in info['formats'] if f.get('acodec') != 'none' and f.get('vcodec') == 'none'),
                         key=lambda x: x.get('abr', 0))

        # Unique filenames
        request_id = str(uuid.uuid4())
        video_path = os.path.join(DOWNLOAD_DIR, f"{request_id}_v.mp4")
        audio_path = os.path.join(DOWNLOAD_DIR, f"{request_id}_a.m4a")
        output_path = os.path.join(DOWNLOAD_DIR, f"{request_id}_o.mp4")

        # Download streams
        with requests.get(best_video['url'], stream=True) as r:
            r.raise_for_status()
            with open(video_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)

        with requests.get(best_audio['url'], stream=True) as r:
            r.raise_for_status()
            with open(audio_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)

        # Merge
        ffmpeg_cmd = ['ffmpeg', '-i', video_path, '-i', audio_path, '-c', 'copy', output_path]
        process = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if process.returncode != 0:
            raise HTTPException(status_code=500, detail="Failed to merge video/audio.")

        # Track expiry (1h)
        filename = os.path.basename(output_path)
        FILE_EXPIRY[filename] = time.time() + 3600

        # ✅ Force direct download
        safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in info.get("title", "video"))
        return FileResponse(
            path=output_path,
            media_type="application/octet-stream",
            filename=f"{safe_title}.mp4",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.mp4"'}
        )

    except Exception as e:
        logging.error(f"Merge error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
