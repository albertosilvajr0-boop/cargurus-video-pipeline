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
DB_PATH = PROJECT_ROOT / "pipeline.db"

# Create output directories
for d in [PHOTOS_DIR, STICKERS_DIR, SCRIPTS_DIR, VIDEOS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# API Keys
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# CarGurus
CARGURUS_DEALER_URL = os.getenv(
    "CARGURUS_DEALER_URL",
    "https://www.cargurus.com/Cars/inventorylisting/viewDetailsFilterViewInventoryListing.action"
    "?sourceContext=carGurusHomePageModel&entitySelectingHelper.selectedEntity=d2331"
)
DEALER_NAME = os.getenv("DEALER_NAME", "San Antonio Dodge Chrysler Jeep Ram")

# Pipeline
MAX_VEHICLES = int(os.getenv("MAX_VEHICLES", "0"))
VIDEO_QUALITY = os.getenv("VIDEO_QUALITY", "fast")  # fast, standard, pro
COST_LIMIT = float(os.getenv("COST_LIMIT", "50.0"))
PRIMARY_VIDEO_ENGINE = os.getenv("PRIMARY_VIDEO_ENGINE", "veo")  # veo or sora
VIDEO_ASPECT_RATIO = os.getenv("VIDEO_ASPECT_RATIO", "9:16")
VIDEO_RESOLUTION = os.getenv("VIDEO_RESOLUTION", "720p")

# Cost per second by engine and quality
COST_PER_SECOND = {
    "veo": {
        "fast": 0.15,
        "standard": 0.40,
        "pro": 0.75,
    },
    "sora": {
        "fast": 0.10,      # sora-2 standard 720p
        "standard": 0.30,  # sora-2-pro 720p
        "pro": 0.50,       # sora-2-pro 1080p
    },
}

# Veo generates 8s clips, Sora generates up to 10s
CLIP_DURATION = {
    "veo": 8,
    "sora": 10,
}

TARGET_VIDEO_DURATION = 15  # seconds


def get_cost_per_video(engine: str, quality: str) -> float:
    """Calculate estimated cost for one full 15-second video."""
    cost_sec = COST_PER_SECOND.get(engine, {}).get(quality, 0.15)
    clip_dur = CLIP_DURATION.get(engine, 8)
    num_clips = -(-TARGET_VIDEO_DURATION // clip_dur)  # ceiling division
    total_seconds = num_clips * clip_dur
    return total_seconds * cost_sec


def validate_config():
    """Validate that all required config is present."""
    errors = []
    if not GOOGLE_API_KEY:
        errors.append("GOOGLE_API_KEY is not set")
    if not OPENAI_API_KEY:
        errors.append("OPENAI_API_KEY is not set")
    if not CARGURUS_DEALER_URL:
        errors.append("CARGURUS_DEALER_URL is not set")
    return errors
