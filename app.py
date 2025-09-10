from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import yt_dlp

app = FastAPI()

@app.get("/download")
def download_video(url: str = Query(..., description="Video URL")):
    try:
        ydl_opts = {
            'skip_download': True,  # don't actually download, just extract
            'quiet': True,
            'no_warnings': True,
            'format': 'best'
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # Collect formats
        formats = []
        for f in info.get('formats', []):
            if f.get('url'):
                formats.append({
                    'quality': f.get('format_note'),
                    'ext': f.get('ext'),
                    'url': f['url']
                })

        return JSONResponse({
            'title': info.get('title'),
            'thumbnail': info.get('thumbnail'),
            'formats': formats
        })

    except Exception as e:
        return JSONResponse({"error": str(e)})
