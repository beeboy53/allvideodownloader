from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import requests
import subprocess
import os
import uuid
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def cleanup_files(paths: list):
    """Deletes files from the server after the request is finished."""
    for path in paths:
        try:
            if os.path.exists(path):
                os.remove(path)
                logging.info(f"Successfully cleaned up temporary file: {path}")
        except Exception as e:
            logging.error(f"Error cleaning up file {path}: {e}")

@app.get("/")
def home():
    return {"message": "Video Downloader API is running 🚀"}

@app.get("/download_and_merge")
async def download_and_merge(url: str, background_tasks: BackgroundTasks):
    ydl_opts = {'quiet': True}
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # --- ✨ NEW LOGIC: Check for separate streams first ---
        video_only_formats = [f for f in info.get('formats', []) if f.get('vcodec') != 'none' and f.get('acodec') == 'none' and f.get('url')]
        audio_only_formats = [f for f in info.get('formats', []) if f.get('acodec') != 'none' and f.get('vcodec') == 'none' and f.get('url')]

        # --- PATH 1: If separate streams exist, merge them ---
        if video_only_formats and audio_only_formats:
            logging.info("Separate video and audio streams found. Proceeding with merge.")
            
            best_video = max(video_only_formats, key=lambda x: x.get('height', 0))
            best_audio = max(audio_only_formats, key=lambda x: x.get('abr', 0))

            request_id = str(uuid.uuid4())
            video_path = f"/tmp/{request_id}_video.mp4"
            audio_path = f"/tmp/{request_id}_audio.m4a"
            output_path = f"/tmp/{request_id}_output.mp4"
            
            files_to_cleanup = [video_path, audio_path, output_path]
            background_tasks.add_task(cleanup_files, files_to_cleanup)

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
            
            ffmpeg_command = ['ffmpeg', '-i', video_path, '-i', audio_path, '-c', 'copy', output_path]
            
            process = subprocess.run(ffmpeg_command, capture_output=True, text=True)
            if process.returncode != 0:
                logging.error(f"FFmpeg Error: {process.stderr}")
                raise HTTPException(status_code=500, detail="Failed to merge video and audio.")
            
            return FileResponse(
                path=output_path,
                media_type='video/mp4',
                filename=f"{info.get('title', 'video')}.mp4"
            )

        # --- PATH 2: If no separate streams, find the best pre-merged file ---
        else:
            logging.info("No separate streams found. Looking for a pre-merged format.")
            merged_formats = [f for f in info.get('formats', []) if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('url')]

            if not merged_formats:
                raise HTTPException(status_code=404, detail="No downloadable video formats found.")

            best_format = max(merged_formats, key=lambda x: x.get('height', 0))
            
            # Instead of downloading and re-serving, just redirect the user's browser to the direct URL.
            # This is much faster and more efficient.
            return RedirectResponse(url=best_format['url'])

    except Exception as e:
        logging.error(f"An error occurred for URL {url}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
