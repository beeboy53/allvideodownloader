from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp, requests, subprocess, os, uuid, logging, time

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def cleanup_files(paths: list):
    for path in paths:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            logging.error(f"Error cleaning up {path}: {e}")


@app.get("/")
def home():
    return {"message": "Video Downloader API is running 🚀"}


@app.get("/info")
async def get_video_info(url: str):
    """
    Get video info + formats.
    Also injects a merged MP4 option (via /merge_streams).
    """
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = []
        for f in info.get("formats", []):
            if f.get("url"):
                formats.append({
                    "url": f["url"],
                    "ext": f.get("ext"),
                    "height": f.get("height"),
                    "acodec": f.get("acodec"),
                    "vcodec": f.get("vcodec"),
                    "quality": f.get("format_note") or (f.get("height") and f"{f['height']}p") or "Audio",
                })

        # ✅ Add merged MP4 option at the top
        # inside get_video_info
        merge_url = f"https://allvideodownloader-production-26b1.up.railway.app/merge_streams?url={url}"
        formats.insert(0, {
            "url": merge_url,
            "ext": "mp4",
            "height": max((f.get("height") or 0) for f in formats),
            "acodec": "merged",
            "vcodec": "merged",
            "quality": "⭐ Best Quality (Merged MP4)"
        })

        return {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "formats": formats
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Info error: {str(e)}")


@app.get("/merge_streams")
async def merge_streams(url: str, background_tasks: BackgroundTasks):
    """
    Download best video+audio separately and merge into MP4.
    """
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)

        video_formats = [f for f in info['formats'] if f.get('vcodec') != 'none' and f.get('acodec') == 'none']
        audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none' and f.get('vcodec') == 'none']

        if not video_formats or not audio_formats:
            raise HTTPException(status_code=400, detail="No separate video/audio streams found.")

        best_video = max(video_formats, key=lambda x: x.get('height') or 0)
        best_audio = max(audio_formats, key=lambda x: x.get('abr') or 0)

        request_id = str(uuid.uuid4())
        video_path = f"/tmp/{request_id}_v.mp4"
        audio_path = f"/tmp/{request_id}_a.m4a"
        output_path = f"/tmp/{request_id}_o.mp4"

        files_to_cleanup = [video_path, audio_path, output_path]
        background_tasks.add_task(cleanup_files, files_to_cleanup)

        # Download streams
        with requests.get(best_video['url'], stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(video_path, 'wb') as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)

        with requests.get(best_audio['url'], stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(audio_path, 'wb') as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)

        # Merge
        ffmpeg_cmd = ['ffmpeg', '-y', '-i', video_path, '-i', audio_path, '-c', 'copy', output_path]
        process = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if process.returncode != 0:
            logging.error(f"FFmpeg error: {process.stderr}")
            raise HTTPException(status_code=500, detail="FFmpeg merge failed.")

        filename = os.path.basename(output_path)
        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename=f"{info.get('title','video')}.mp4",
            headers={"Content-Disposition": f'attachment; filename="{info.get("title","video")}.mp4"'}
        )

    except Exception as e:
        logging.error(f"Merge error: {e}")
        raise HTTPException(status_code=500, detail=f"Merge error: {str(e)}")

