"""Tests for video generation and stitching modules."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from utils.database import upsert_vehicle, update_vehicle_status


class TestVideoStitcher:
    def test_stitch_no_clips(self, tmp_path):
        from video_gen.video_stitcher import VideoStitcher

        with patch.object(VideoStitcher, "_check_ffmpeg"):
            stitcher = VideoStitcher()
            result = stitcher.stitch_clips("[]", "test_output")
            assert result is None

    def test_stitch_missing_files(self, tmp_path):
        from video_gen.video_stitcher import VideoStitcher

        clips = json.dumps(["/nonexistent/clip1.mp4", "/nonexistent/clip2.mp4"])

        with patch.object(VideoStitcher, "_check_ffmpeg"):
            stitcher = VideoStitcher()
            result = stitcher.stitch_clips(clips, "test_output")
            assert result is None


class TestConfigSettings:
    def test_get_cost_per_video(self):
        from config.settings import get_cost_per_video

        cost = get_cost_per_video("veo", "fast")
        assert cost > 0
        assert isinstance(cost, float)

    def test_get_cost_per_video_sora(self):
        from config.settings import get_cost_per_video

        cost = get_cost_per_video("sora", "fast")
        assert cost > 0

    def test_validate_config_missing_keys(self):
        from config.settings import validate_config

        with patch("config.settings.GOOGLE_API_KEY", ""), \
             patch("config.settings.OPENAI_API_KEY", ""):
            errors = validate_config()
            assert len(errors) >= 2


class TestFlaskApp:
    @pytest.fixture
    def client(self):
        from app import app
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_health_check(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "healthy"

    def test_api_stats(self, client):
        response = client.get("/api/stats")
        assert response.status_code == 200
        data = response.get_json()
        assert "total_vehicles" in data
        assert "total_cost" in data

    def test_api_vehicles_empty(self, client):
        # Get current count to handle pre-existing test data
        response = client.get("/api/vehicles")
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)

    def test_api_vehicle_not_found(self, client):
        response = client.get("/api/vehicle/9999")
        assert response.status_code == 404

    def test_api_vehicles_with_data(self, client, sample_vehicle_data):
        # Insert and verify it shows up
        vid = upsert_vehicle(sample_vehicle_data)
        response = client.get("/api/vehicles")
        assert response.status_code == 200
        data = response.get_json()
        ids = [v["cargurus_id"] for v in data]
        assert "cg_12345" in ids

    def test_api_vehicles_filter_by_status(self, client, sample_vehicle_data):
        upsert_vehicle(sample_vehicle_data)
        response = client.get("/api/vehicles?status=scraped")
        assert response.status_code == 200
        data = response.get_json()
        assert all(v["status"] == "scraped" for v in data)

    def test_api_pipeline_stats(self, client):
        response = client.get("/api/stats")
        assert response.status_code == 200
        data = response.get_json()
        assert "total_vehicles" in data

    def test_api_retry_single_vehicle(self, client, sample_vehicle_data):
        vid = upsert_vehicle(sample_vehicle_data)
        update_vehicle_status(vid, "error", error_message="test")

        response = client.post(f"/api/retry/{vid}")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"

    def test_api_retry_single_vehicle_not_found(self, client):
        response = client.post("/api/retry/9999")
        assert response.status_code == 404
