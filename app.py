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
async def merge_streams(url: str, background_tasks: BackgroundTasks):
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)

        # Pick best video and audio
        best_video = max(
            (f for f in info['formats'] if f.get('vcodec') != 'none' and f.get('acodec') == 'none'),
            key=lambda x: x.get('height', 0)
        )
        best_audio = max(
            (f for f in info['formats'] if f.get('acodec') != 'none' and f.get('vcodec') == 'none'),
            key=lambda x: x.get('abr', 0)
        )

        request_id = str(uuid.uuid4())
        video_path = f"/tmp/{request_id}_v.mp4"
        audio_path = f"/tmp/{request_id}_a.m4a"
        output_path = f"/tmp/{request_id}_o.mp4"

        files_to_cleanup = [video_path, audio_path, output_path]
        background_tasks.add_task(cleanup_files, files_to_cleanup)

        # Download video + audio chunks
        with requests.get(best_video['url'], stream=True) as r:
            r.raise_for_status()
            with open(video_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        with requests.get(best_audio['url'], stream=True) as r:
            r.raise_for_status()
            with open(audio_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        # Merge with ffmpeg
        ffmpeg_cmd = ['ffmpeg', '-y', '-i', video_path, '-i', audio_path, '-c', 'copy', output_path]
        process = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if process.returncode != 0:
            raise HTTPException(status_code=500, detail="Failed to merge files.")

        # ✅ Instead of sending file, return JSON link
        file_id = os.path.basename(output_path)
        return {
            "status": "ok",
            "title": info.get("title"),
            "download_url": f"/files/{file_id}",
            "expires_in": 3600
        }

    except Exception as e:
        logging.error(f"Error in /merge_streams: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/files/{filename}")
def serve_file(filename: str):
    file_path = f"/tmp/{filename}"
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(file_path, media_type="video/mp4", filename=filename)


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

