from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

app = FastAPI()

# ✅ Enable CORS so WordPress (and browsers) can fetch from your API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # You can restrict later, e.g., ["https://yourdomain.com"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"message": "Video Downloader API is running 🚀"}

@app.get("/download")
def download_video(url: str = Query(..., description="Video URL to download")):
    try:
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = []
        for f in info.get("formats", []):
            # Only include playable formats
            if f.get("url"):
                formats.append({
                    "quality": f.get("format_note") or f.get("height"),
                    "ext": f.get("ext"),
                    "url": f.get("url")
                })

        return {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "formats": formats
        }
    except Exception as e:
        return {"error": str(e)}
