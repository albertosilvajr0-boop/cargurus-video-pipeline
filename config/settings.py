"""Configuration management for the pipeline."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
PHOTOS_DIR = OUTPUT_DIR / "photos"
STICKERS_DIR = OUTPUT_DIR / "stickers"
SCRIPTS_DIR = OUTPUT_DIR / "scripts"
VIDEOS_DIR = OUTPUT_DIR / "videos"
UPLOADS_DIR = OUTPUT_DIR / "uploads"
BRANDING_DIR = OUTPUT_DIR / "branding"
DB_PATH = PROJECT_ROOT / "pipeline.db"

# Create output directories
for d in [PHOTOS_DIR, STICKERS_DIR, SCRIPTS_DIR, VIDEOS_DIR, UPLOADS_DIR, BRANDING_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# API Keys
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Dealer branding (set once, used on every video)
DEALER_NAME = os.getenv("DEALER_NAME", "San Antonio Dodge Chrysler Jeep Ram")
DEALER_PHONE = os.getenv("DEALER_PHONE", "")
DEALER_ADDRESS = os.getenv("DEALER_ADDRESS", "")
DEALER_WEBSITE = os.getenv("DEALER_WEBSITE", "")
DEALER_LOGO_PATH = os.getenv("DEALER_LOGO_PATH", "")  # Path to logo PNG with transparency

# Pipeline
MAX_VEHICLES = int(os.getenv("MAX_VEHICLES", "0"))
VIDEO_QUALITY = os.getenv("VIDEO_QUALITY", "fast")  # fast, standard, pro
COST_LIMIT = float(os.getenv("COST_LIMIT", "50.0"))
PRIMARY_VIDEO_ENGINE = "sora"
VIDEO_ASPECT_RATIO = os.getenv("VIDEO_ASPECT_RATIO", "9:16")
VIDEO_RESOLUTION = os.getenv("VIDEO_RESOLUTION", "720p")
TARGET_VIDEO_DURATION = 25  # seconds (20s AI clip + 5s CTA outro)

# Overlay settings
OVERLAY_FONT = os.getenv("OVERLAY_FONT", "")  # Path to .ttf font, or empty for default
OVERLAY_CTA_TEXT = os.getenv("OVERLAY_CTA_TEXT", "Call Today!")
OVERLAY_PRICE_POSITION = os.getenv("OVERLAY_PRICE_POSITION", "top-right")  # top-left, top-right
OVERLAY_BOTTOM_BAR_COLOR = os.getenv("OVERLAY_BOTTOM_BAR_COLOR", "#000000CC")  # RGBA hex

# Cost per second by engine and quality
COST_PER_SECOND = {
    "sora": {
        "fast": 0.10,
        "standard": 0.30,
        "pro": 0.50,
    },
}

# Sora 2 accepts 5, 10, 15, or 20s clips
CLIP_DURATION = {
    "sora": 20,
}


def get_cost_per_video(engine: str, quality: str) -> float:
    """Calculate estimated cost for one video (single clip in new workflow)."""
    cost_sec = COST_PER_SECOND.get(engine, {}).get(quality, 0.15)
    clip_dur = CLIP_DURATION.get(engine, 8)
    return clip_dur * cost_sec


def validate_config():
    """Validate that all required config is present."""
    errors = []
    if not GOOGLE_API_KEY:
        errors.append("GOOGLE_API_KEY is not set (needed for Gemini extraction)")
    if not OPENAI_API_KEY:
        errors.append("OPENAI_API_KEY is not set (needed for Sora video generation)")
    return errors
