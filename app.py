from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

app = FastAPI()

# ✅ Enable CORS so WordPress (and browsers) can fetch from your API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # You can restrict later, e.g., ["https://yourdomain.com"]
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
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats_list = []
        for f in info.get("formats", []):
            # We only want formats with a direct URL that we can use
            if f.get("url"):
                formats_list.append({
                    "url": f.get("url"),
                    "ext": f.get("ext"),
                    "height": f.get("height"),
                    "width": f.get("width"),
                    # These are the crucial keys to check for audio/video presence
                    "acodec": f.get("acodec"),
                    "vcodec": f.get("vcodec"),
                    "quality": f.get("format_note") or (f.get("height") and f"{f.get('height')}p"),
                })
        
        # --- ✨ KEY CHANGE: SORTING THE FORMATS ---
        # We sort by two criteria in descending order:
        # 1. (Primary) Prioritize formats that have BOTH video and audio.
        # 2. (Secondary) Sort by resolution (height) from highest to lowest.
        def sort_key(f):
            has_video = f.get("vcodec") is not None and f["vcodec"] != "none"
            has_audio = f.get("acodec") is not None and f["acodec"] != "none"
            height = f.get("height") or 0
            # This tuple ensures sorting happens in the desired order
            return (has_video and has_audio, height)

        # 'reverse=True' makes the highest values (True, 1080p) appear first
        sorted_formats = sorted(formats_list, key=sort_key, reverse=True)

        return {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "formats": sorted_formats  # ✅ Return the newly sorted list
        }
    except Exception as e:
        return {"error": str(e)}
