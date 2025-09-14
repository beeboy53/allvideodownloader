from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import requests
import subprocess
import os
import uuid # For creating unique temporary filenames
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

# --- ✨ HELPER FUNCTION FOR CLEANUP ---
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
    """
    Finds the best video and audio, downloads them, merges them with FFmpeg,
    and returns the final video file for download.
    """
    ydl_opts = {'quiet': True}
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # --- ✨ 1. Find the best video and audio streams ---
        best_video = max(
            (f for f in info['formats'] if f.get('vcodec') != 'none' and f.get('acodec') == 'none'),
            key=lambda x: x.get('height', 0)
        )
        best_audio = max(
            (f for f in info['formats'] if f.get('acodec') != 'none' and f.get('vcodec') == 'none'),
            key=lambda x: x.get('abr', 0) # abr = average bitrate
        )

        if not best_video or not best_audio:
            raise HTTPException(status_code=404, detail="Suitable video/audio streams not found for merging.")

        # --- ✨ 2. Download files to a temporary location ---
        # Generate unique filenames to avoid conflicts
        request_id = str(uuid.uuid4())
        video_path = f"/tmp/{request_id}_video.mp4"
        audio_path = f"/tmp/{request_id}_audio.m4a"
        output_path = f"/tmp/{request_id}_output.mp4"
        
        # Add all paths to a list for easy cleanup
        files_to_cleanup = [video_path, audio_path, output_path]
        background_tasks.add_task(cleanup_files, files_to_cleanup)

        logging.info(f"Downloading video to {video_path}")
        with requests.get(best_video['url'], stream=True) as r:
            r.raise_for_status()
            with open(video_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        logging.info(f"Downloading audio to {audio_path}")
        with requests.get(best_audio['url'], stream=True) as r:
            r.raise_for_status()
            with open(audio_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        
        # --- ✨ 3. Merge files with FFmpeg ---
        logging.info("Merging files with FFmpeg...")
        ffmpeg_command = [
            'ffmpeg',
            '-i', video_path,
            '-i', audio_path,
            '-c', 'copy', # Copies streams without re-encoding (very fast)
            output_path
        ]
        
        # Run the command
        process = subprocess.run(ffmpeg_command, capture_output=True, text=True)
        if process.returncode != 0:
            logging.error(f"FFmpeg Error: {process.stderr}")
            raise HTTPException(status_code=500, detail="Failed to merge video and audio.")
        
        logging.info(f"Successfully created merged file: {output_path}")

        # --- ✨ 4. Serve the merged file ---
        # The cleanup task will run after the file is sent
        return FileResponse(
            path=output_path,
            media_type='video/mp4',
            filename=f"{info.get('title', 'video')}.mp4"
        )

    except Exception as e:
        logging.error(f"An error occurred during download/merge for URL {url}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
