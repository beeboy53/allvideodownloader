from fastapi import FastAPI, Query, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import requests
import subprocess
import os
import uuid
import logging
from urllib.parse import urlencode

logging.basicConfig(level=logging.INFO)
app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

def cleanup_files(paths: list):
    for path in paths:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            logging.error(f"Error cleaning up file {path}: {e}")

@app.get("/")
def home():
    return {"message": "Video Downloader API is running 🚀"}

@app.get("/info")
async def get_video_info(request: Request, url: str):
    """
    Gets video info, all formats, and creates a special 'merge' URL if needed.
    This is the main endpoint your frontend will call.
    """
    ydl_opts = {'quiet': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        all_formats = []
        
        # --- ✨ INTELLIGENTLY CREATE A MERGED OPTION ---
        video_only = [f for f in info.get('formats', []) if f.get('vcodec') != 'none' and f.get('acodec') == 'none' and f.get('url')]
        audio_only = [f for f in info.get('formats', []) if f.get('acodec') != 'none' and f.get('vcodec') == 'none' and f.get('url')]

        if video_only and audio_only:
            # Build the URL for our /merge_streams endpoint
            query_params = urlencode({'url': url})
            merge_url = str(request.base_url) + "merge_streams?" + query_params
            
            # Create a "virtual" format for the merged option
            all_formats.append({
                'quality': 'Best Quality (Merged)',
                'ext': 'mp4',
                'url': merge_url,
                'vcodec': 'merged', # Custom identifier for the frontend
                'acodec': 'merged',
                'height': max(v.get('height', 0) for v in video_only) # Get the max height for sorting
            })

        # Add all other real formats from yt-dlp to the list
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
        
        # Sort the final list to put the best options at the top
        sorted_formats = sorted(all_formats, key=lambda x: x.get('height') or 0, reverse=True)

        return {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "formats": sorted_formats
        }
    except Exception as e:
        logging.error(f"Error in /info for URL {url}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/merge_streams")
async def merge_streams(url: str, background_tasks: BackgroundTasks):
    """
    This is the 'worker' endpoint. It downloads, merges, and serves the file.
    """
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
        
        best_video = max((f for f in info['formats'] if f.get('vcodec') != 'none' and f.get('acodec') == 'none'), key=lambda x: x.get('height', 0))
        best_audio = max((f for f in info['formats'] if f.get('acodec') != 'none' and f.get('vcodec') == 'none'), key=lambda x: x.get('abr', 0))

        request_id = str(uuid.uuid4())
        video_path = f"/tmp/{request_id}_video.mp4"
        audio_path = f"/tmp/{request_id}_audio.m4a"
        output_path = f"/tmp/{request_id}_output.mp4"
        
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
        
        ffmpeg_cmd = ['ffmpeg', '-i', video_path, '-i', audio_path, '-c', 'copy', output_path]
        process = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if process.returncode != 0:
            raise HTTPException(status_code=500, detail="Failed to merge files.")

        return FileResponse(path=output_path, media_type='video/mp4', filename=f"{info.get('title', 'video')}.mp4")
    except Exception as e:
        logging.error(f"Error in /merge_streams for URL {url}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
