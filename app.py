from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"message": "Video Downloader API is running 🚀"}

@app.get("/download")
def download_video(url: str = Query(..., description="Video URL to download")):
    # --- ✨ NEW: Enhanced yt_dlp Options ---
    ydl_opts = {
        'quiet': True,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'noplaylist': True,
        'ignoreerrors': True,
        # Spoof a browser User-Agent to avoid being blocked
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.5',
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # --- ✨ NEW: Robust Check for Formats ---
        # Check if info was extracted and if there are any formats available
        if not info or not info.get("formats"):
            logging.warning(f"No downloadable formats found for URL: {url}")
            raise HTTPException(
                status_code=404, 
                detail="No downloadable video found. The website may not be supported or the content is protected."
            )

        formats_list = []
        for f in info.get("formats", []):
            if f.get("url"):
                formats_list.append({
                    "url": f.get("url"),
                    "ext": f.get("ext"),
                    "height": f.get("height"),
                    "width": f.get("width"),
                    "acodec": f.get("acodec"),
                    "vcodec": f.get("vcodec"),
                    "quality": f.get("format_note") or (f.get("height") and f"{f.get('height')}p"),
                })

        # If after all that, our list is still empty, raise an error
        if not formats_list:
             raise HTTPException(
                status_code=404, 
                detail="Could not extract any direct download links."
            )

        def sort_key(f):
            has_video = f.get("vcodec") is not None and f["vcodec"] != "none"
            has_audio = f.get("acodec") is not None and f["acodec"] != "none"
            height = f.get("height") or 0
            return (has_video and has_audio, height)

        sorted_formats = sorted(formats_list, key=sort_key, reverse=True)

        return {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "formats": sorted_formats
        }
    except HTTPException as http_exc:
        # Re-raise HTTP exceptions to let FastAPI handle them
        raise http_exc
    except Exception as e:
        logging.error(f"An unexpected error occurred for URL {url}: {str(e)}")
        # Return a generic server error for other exceptions
        raise HTTPException(status_code=500, detail=f"An internal error occurred: {str(e)}")
