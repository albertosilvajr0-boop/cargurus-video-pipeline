"""Shared test fixtures for the CarGurus video pipeline tests."""

import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Use a temporary database for every test."""
    db_path = tmp_path / "test_pipeline.db"

    # Patch DB_PATH in both the settings module and the database module
    # (database.py imports DB_PATH at module level, so we need both)
    monkeypatch.setattr("config.settings.DB_PATH", db_path)
    monkeypatch.setattr("utils.database.DB_PATH", db_path)

    # Also redirect output dirs to tmp
    for attr in ("OUTPUT_DIR", "PHOTOS_DIR", "STICKERS_DIR", "SCRIPTS_DIR", "VIDEOS_DIR"):
        d = tmp_path / attr.lower()
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(f"config.settings.{attr}", d)

    # Initialize the schema for this test
    from utils.database import init_db
    init_db()

    yield db_path


@pytest.fixture
def sample_vehicle_data():
    """Return a realistic vehicle data dict for testing."""
    return {
        "cargurus_id": "cg_12345",
        "vin": "1C6SRFFT0PN654321",
        "year": 2024,
        "make": "Ram",
        "model": "1500",
        "trim": "Big Horn",
        "price": 42995.0,
        "mileage": 12500,
        "exterior_color": "Bright White",
        "interior_color": "Black",
        "engine": "5.7L V8 HEMI",
        "transmission": "8-Speed Automatic",
        "drivetrain": "4WD",
        "listing_url": "https://www.cargurus.com/Cars/inventorylisting/viewDetailsFilterViewInventoryListing.action?inventoryListing=12345",
    }


@pytest.fixture
def sample_script():
    """Return a realistic parsed video script dict."""
    return {
        "hook": "Power meets luxury on the open road",
        "scenes": [
            {
                "timestamp": "0:00-0:03",
                "visual_prompt": "Aerial drone shot revealing a white 2024 Ram 1500...",
                "text_overlay": "2024 RAM 1500 BIG HORN",
                "audio_cue": "Epic cinematic bass drop",
            },
            {
                "timestamp": "0:03-0:08",
                "visual_prompt": "Slow dolly shot along the side of the Ram...",
                "text_overlay": "$42,995",
                "audio_cue": "Building orchestral strings",
            },
            {
                "timestamp": "0:08-0:13",
                "visual_prompt": "Interior reveal -- sweeping shot of black leather interior...",
                "text_overlay": "5.7L HEMI V8",
                "audio_cue": "Engine roar transition",
            },
            {
                "timestamp": "0:13-0:15",
                "visual_prompt": "Hero shot of Ram driving into sunset...",
                "text_overlay": "Visit Today",
                "audio_cue": "Final impact hit",
            },
        ],
        "cta": "Visit San Antonio Dodge -- This Won't Last!",
        "veo_master_prompt": "Cinematic hero shot of a bright white 2024 Ram 1500 Big Horn...",
        "veo_extension_prompt": "Interior reveal of 2024 Ram 1500 with black leather...",
        "caption": "Built for the bold. 2024 Ram 1500 Big Horn #Ram #Truck #SanAntonio",
        "target_emotion": "excitement",
    }
