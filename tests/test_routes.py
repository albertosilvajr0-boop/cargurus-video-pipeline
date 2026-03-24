"""Integration tests for critical API endpoints.

Tests the Flask routes with a test client, using a temporary database
to avoid polluting production data.
"""

import io
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Override DB_PATH before any database import
import tempfile
_test_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)

import config.settings as settings
settings.DB_PATH = Path(_test_db.name)

from app import app
from utils.database import init_db


@pytest.fixture
def client():
    """Flask test client with a fresh temporary database."""
    app.config["TESTING"] = True
    init_db()
    with app.test_client() as client:
        yield client


class TestHealthAndDiagnostics:
    def test_health_check(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "healthy"
        assert "timestamp" in data

    def test_stats_endpoint(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total_vehicles" in data
        assert "total_cost" in data

    def test_vehicles_list_empty(self, client):
        resp = client.get("/api/vehicles")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_vehicle_not_found(self, client):
        resp = client.get("/api/vehicle/99999")
        assert resp.status_code == 404

    def test_delete_vehicle_not_found(self, client):
        resp = client.delete("/api/vehicle/99999")
        assert resp.status_code == 404

    def test_costs_endpoint(self, client):
        resp = client.get("/api/costs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total_cost" in data
        assert "budget_limit" in data

    def test_gcs_status(self, client):
        resp = client.get("/api/gcs/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "enabled" in data


class TestUploadValidation:
    def test_upload_no_photos(self, client):
        resp = client.post("/api/upload")
        assert resp.status_code == 400
        assert "photo" in resp.get_json()["error"].lower()

    def test_upload_empty_photos(self, client):
        resp = client.post("/api/upload", data={"photos[]": (io.BytesIO(b""), "")})
        assert resp.status_code == 400

    def test_vin_missing(self, client):
        resp = client.post("/api/vin", json={})
        assert resp.status_code == 400

    def test_vin_invalid(self, client):
        resp = client.post("/api/vin", json={"vin": "TOOSHORT"})
        assert resp.status_code == 400
        assert "Invalid VIN" in resp.get_json()["error"]

    def test_vin_decode_only_invalid(self, client):
        resp = client.post("/api/vin/decode", json={"vin": "BAD"})
        assert resp.status_code == 400


class TestBranding:
    def test_get_branding_default(self, client):
        resp = client.get("/api/branding")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "dealer_name" in data

    def test_save_branding(self, client):
        resp = client.post("/api/branding", data={
            "dealer_name": "Test Dealer",
            "phone": "555-1234",
            "address": "123 Test St",
        })
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "saved"

        # Verify it persisted
        resp = client.get("/api/branding")
        data = resp.get_json()
        assert data["phone"] == "555-1234"


class TestPromptTemplates:
    def test_list_templates(self, client):
        # Seed default templates first
        from utils.database import seed_default_templates
        seed_default_templates()
        resp = client.get("/api/prompt-templates")
        assert resp.status_code == 200
        templates = resp.get_json()
        assert len(templates) >= 1

    def test_create_template(self, client):
        resp = client.post("/api/prompt-templates", json={
            "display_name": "Test Template",
            "prompt_text": "Generate a cinematic video of {vehicle_name}",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "created"
        template_id = data["id"]

        # Update
        resp = client.put(f"/api/prompt-templates/{template_id}", json={
            "display_name": "Updated Template",
            "prompt_text": "Updated prompt text",
        })
        assert resp.status_code == 200

        # Delete
        resp = client.delete(f"/api/prompt-templates/{template_id}")
        assert resp.status_code == 200

    def test_create_template_missing_fields(self, client):
        resp = client.post("/api/prompt-templates", json={"display_name": "No text"})
        assert resp.status_code == 400


class TestMediaLibrary:
    def test_media_list_empty(self, client):
        resp = client.get("/api/media")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_media_groups_empty(self, client):
        resp = client.get("/api/media/groups")
        assert resp.status_code == 200

    def test_media_upload_no_files(self, client):
        resp = client.post("/api/media/upload")
        assert resp.status_code == 400

    def test_media_generate_no_ids(self, client):
        resp = client.post("/api/media/generate", json={})
        assert resp.status_code == 400

    def test_media_generate_invalid_ids(self, client):
        resp = client.post("/api/media/generate", json={"media_ids": [99999]})
        assert resp.status_code == 404


class TestPeople:
    def test_people_list_empty(self, client):
        resp = client.get("/api/people")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_create_person(self, client):
        resp = client.post("/api/people", json={"name": "John Doe"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "created"
        person_id = data["id"]

        # Rename
        resp = client.patch(f"/api/people/{person_id}", json={"name": "Jane Doe"})
        assert resp.status_code == 200

        # Delete
        resp = client.delete(f"/api/people/{person_id}")
        assert resp.status_code == 200

    def test_create_person_no_name(self, client):
        resp = client.post("/api/people", json={})
        assert resp.status_code == 400


class TestJobStatus:
    def test_jobs_empty(self, client):
        resp = client.get("/api/jobs")
        assert resp.status_code == 200

    def test_job_not_found(self, client):
        resp = client.get("/api/job/nonexistent_job_123")
        assert resp.status_code == 404


class TestRetry:
    def test_retry_vehicle_not_found(self, client):
        resp = client.post("/api/retry/99999", json={})
        assert resp.status_code == 404

    def test_retry_all_empty(self, client):
        resp = client.post("/api/retry-all", json={})
        assert resp.status_code == 200
        assert resp.get_json()["reset_count"] == 0


class TestDatabaseFieldWhitelist:
    """Verify that SQL injection via field names is prevented."""

    def test_upsert_rejects_bad_field(self):
        from utils.database import _validate_field_names, VEHICLES_ALLOWED_FIELDS
        dirty = {"make": "Toyota", "'; DROP TABLE vehicles; --": "bad"}
        clean = _validate_field_names(dirty, VEHICLES_ALLOWED_FIELDS)
        assert "make" in clean
        assert "'; DROP TABLE vehicles; --" not in clean
