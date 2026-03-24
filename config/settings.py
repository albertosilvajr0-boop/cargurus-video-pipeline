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
MEDIA_DIR = OUTPUT_DIR / "media"
PEOPLE_DIR = OUTPUT_DIR / "people"
DB_PATH = PROJECT_ROOT / "pipeline.db"

# Create output directories
for d in [PHOTOS_DIR, STICKERS_DIR, SCRIPTS_DIR, VIDEOS_DIR, UPLOADS_DIR, BRANDING_DIR, MEDIA_DIR, PEOPLE_DIR]:
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
SORA_CLIP_DURATION = int(os.getenv("SORA_CLIP_DURATION", "20"))  # seconds (5, 10, 15, or 20)
SORA_MAX_WAIT_SECONDS = int(os.getenv("SORA_MAX_WAIT_SECONDS", "900"))  # 15-minute safety limit
VEO_MAX_WAIT_SECONDS = int(os.getenv("VEO_MAX_WAIT_SECONDS", "900"))

# Upload validation
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "20"))

# Overlay settings
OVERLAY_FONT = os.getenv("OVERLAY_FONT", "")  # Path to .ttf font, or empty for default
OVERLAY_CTA_TEXT = os.getenv("OVERLAY_CTA_TEXT", "Call Today!")
OVERLAY_PRICE_POSITION = os.getenv("OVERLAY_PRICE_POSITION", "top-right")  # top-left, top-right
OVERLAY_BOTTOM_BAR_COLOR = os.getenv("OVERLAY_BOTTOM_BAR_COLOR", "#000000CC")  # RGBA hex

# Google Cloud Storage (for video persistence)
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "")  # e.g., "my-dealer-videos"
GCS_CREDENTIALS_PATH = os.getenv("GCS_CREDENTIALS_PATH", "")  # Path to service account JSON (optional if using ADC)
GCS_PUBLIC_URL_BASE = os.getenv("GCS_PUBLIC_URL_BASE", "")  # Custom domain or leave empty for default GCS URL

# Flat cost per video by engine and quality (actual API billing rates)
COST_PER_VIDEO = {
    "sora": {
        "fast": 1.20,
        "standard": 3.00,
        "pro": 6.00,
    },
    "veo": {
        "fast": 0.50,
        "standard": 1.50,
        "pro": 4.00,
    },
}

# Gemini API cost per extraction call
GEMINI_COST_PER_CALL = 0.02


def get_cost_per_video(engine: str, quality: str) -> float:
    """Get the flat cost for one video generation."""
    return COST_PER_VIDEO.get(engine, {}).get(quality, 1.20)


def validate_config():
    """Validate that all required config is present."""
    errors = []
    if not GOOGLE_API_KEY:
        errors.append("GOOGLE_API_KEY is not set (needed for Gemini extraction)")
    if not OPENAI_API_KEY:
        errors.append("OPENAI_API_KEY is not set (needed for Sora video generation)")
    return errors
