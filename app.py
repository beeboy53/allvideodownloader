# main.py

from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
# ... (keep all your other imports)
import yt_dlp
# ...

# ... (keep your app setup and the cleanup_files function)

@app.get("/download_and_merge")
async def download_and_merge(url: str, background_tasks: BackgroundTasks):
    """
    Handles both pre-merged and separate video/audio streams robustly.
    """
    ydl_opts = {'quiet': True}
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # --- ✨ 1. CHECK FOR PRE-MERGED FORMATS FIRST ---
        merged_formats = [
            f for f in info['formats'] 
            if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('ext') == 'mp4'
        ]

        if merged_formats:
            best_merged_format = max(merged_formats, key=lambda x: x.get('height', 0))
            logging.info(f"Found a pre-merged format. Downloading directly from {best_merged_format['url']}")
            
            request_id = str(uuid.uuid4())
            output_path = f"/tmp/{request_id}_output.mp4"
            background_tasks.add_task(cleanup_files, [output_path])

            with requests.get(best_merged_format['url'], stream=True) as r:
                r.raise_for_status()
                with open(output_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            
            return FileResponse(
                path=output_path,
                media_type='video/mp4',
                filename=f"{info.get('title', 'video')}.mp4"
            )

        # --- ✨ 2. IF NO PRE-MERGED, SAFELY FIND SEPARATE STREAMS ---
        video_streams = [f for f in info['formats'] if f.get('vcodec') != 'none' and f.get('acodec') == 'none']
        audio_streams = [f for f in info['formats'] if f.get('acodec') != 'none' and f.get('vcodec') == 'none']

        # This check prevents the "max() arg is an empty sequence" error
        if not video_streams or not audio_streams:
            raise HTTPException(status_code=404, detail="Could not find compatible separate video and audio streams to merge.")

        best_video = max(video_streams, key=lambda x: x.get('height', 0))
        best_audio = max(audio_streams, key=lambda x: x.get('abr', 0))
        
        # --- The rest of the download and merge logic remains the same ---
        request_id = str(uuid.uuid4())
        video_path = f"/tmp/{request_id}_video.mp4"
        audio_path = f"/tmp/{request_id}_audio.m4a"
        output_path = f"/tmp/{request_id}_output.mp4"
        
        files_to_cleanup = [video_path, audio_path, output_path]
        background_tasks.add_task(cleanup_files, files_to_cleanup)

        logging.info(f"Downloading separate video to {video_path}")
        with requests.get(best_video['url'], stream=True) as r:
            r.raise_for_status()
            with open(video_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        logging.info(f"Downloading separate audio to {audio_path}")
        with requests.get(best_audio['url'], stream=True) as r:
            r.raise_for_status()
            with open(audio_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        
        logging.info("Merging files with FFmpeg...")
        ffmpeg_command = ['ffmpeg', '-i', video_path, '-i', audio_path, '-c', 'copy', output_path]
        
        process = subprocess.run(ffmpeg_command, capture_output=True, text=True)
        if process.returncode != 0:
            logging.error(f"FFmpeg Error: {process.stderr}")
            raise HTTPException(status_code=500, detail="Failed to merge video and audio.")
        
        logging.info(f"Successfully created merged file: {output_path}")

        return FileResponse(
            path=output_path,
            media_type='video/mp4',
            filename=f"{info.get('title', 'video')}.mp4"
        )

    except Exception as e:
        logging.error(f"An error occurred during download/merge for URL {url}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
