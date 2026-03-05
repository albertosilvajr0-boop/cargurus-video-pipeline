"""Tests for the SQLite database module."""

from utils.database import (
    upsert_vehicle,
    update_vehicle_status,
    get_vehicles_by_status,
    get_all_vehicles,
    get_pipeline_stats,
    log_cost,
    get_total_spend,
    retry_failed_vehicles,
    retry_vehicle_by_id,
)


class TestUpsertVehicle:
    def test_insert_new_vehicle(self, sample_vehicle_data):
        vid = upsert_vehicle(sample_vehicle_data)
        assert vid > 0

        vehicles = get_all_vehicles()
        assert len(vehicles) == 1
        assert vehicles[0]["cargurus_id"] == "cg_12345"
        assert vehicles[0]["year"] == 2024

    def test_upsert_updates_existing(self, sample_vehicle_data):
        vid1 = upsert_vehicle(sample_vehicle_data)

        # Update the price
        sample_vehicle_data["price"] = 39995.0
        vid2 = upsert_vehicle(sample_vehicle_data)

        assert vid1 == vid2
        vehicles = get_all_vehicles()
        assert len(vehicles) == 1
        assert vehicles[0]["price"] == 39995.0

    def test_insert_multiple_vehicles(self, sample_vehicle_data):
        upsert_vehicle(sample_vehicle_data)

        vehicle2 = sample_vehicle_data.copy()
        vehicle2["cargurus_id"] = "cg_67890"
        vehicle2["vin"] = "1C6SRFFT0PN999999"
        upsert_vehicle(vehicle2)

        vehicles = get_all_vehicles()
        assert len(vehicles) == 2


class TestUpdateVehicleStatus:
    def test_update_status(self, sample_vehicle_data):
        vid = upsert_vehicle(sample_vehicle_data)
        update_vehicle_status(vid, "photos_downloaded")

        vehicles = get_vehicles_by_status("photos_downloaded")
        assert len(vehicles) == 1
        assert vehicles[0]["id"] == vid

    def test_update_status_with_extra_fields(self, sample_vehicle_data):
        vid = upsert_vehicle(sample_vehicle_data)
        update_vehicle_status(vid, "error", error_message="Test error")

        vehicles = get_vehicles_by_status("error")
        assert len(vehicles) == 1
        assert vehicles[0]["error_message"] == "Test error"


class TestGetVehiclesByStatus:
    def test_filter_by_status(self, sample_vehicle_data):
        vid = upsert_vehicle(sample_vehicle_data)

        # Default status is 'scraped'
        scraped = get_vehicles_by_status("scraped")
        assert len(scraped) == 1

        empty = get_vehicles_by_status("video_complete")
        assert len(empty) == 0

    def test_returns_dicts(self, sample_vehicle_data):
        upsert_vehicle(sample_vehicle_data)
        vehicles = get_vehicles_by_status("scraped")
        assert isinstance(vehicles[0], dict)


class TestGetPipelineStats:
    def test_empty_stats(self):
        stats = get_pipeline_stats()
        assert stats["total_vehicles"] == 0
        assert stats["videos_completed"] == 0
        assert stats["total_cost"] == 0.0

    def test_stats_with_vehicles(self, sample_vehicle_data):
        vid = upsert_vehicle(sample_vehicle_data)
        update_vehicle_status(vid, "video_complete", video_path="/path/to/video.mp4", video_cost=2.40)

        stats = get_pipeline_stats()
        assert stats["total_vehicles"] == 1
        assert stats["videos_completed"] == 1
        assert stats["total_cost"] == 2.40
        assert stats["by_status"]["video_complete"] == 1


class TestCostLog:
    def test_log_and_retrieve_cost(self, sample_vehicle_data):
        vid = upsert_vehicle(sample_vehicle_data)
        log_cost(vid, "veo", "fast", 16.0, 2.40, "video_generation")

        total = get_total_spend()
        assert total == 2.40

    def test_multiple_costs(self, sample_vehicle_data):
        vid = upsert_vehicle(sample_vehicle_data)
        log_cost(vid, "gemini", "flash", 0, 0.001, "script_generation")
        log_cost(vid, "veo", "fast", 16.0, 2.40, "video_generation")

        total = get_total_spend()
        assert abs(total - 2.401) < 0.001


class TestRetryFailedVehicles:
    def test_retry_all_failed(self, sample_vehicle_data):
        vid = upsert_vehicle(sample_vehicle_data)
        update_vehicle_status(vid, "error", error_message="Test failure")

        count = retry_failed_vehicles()
        assert count == 1

        vehicles = get_vehicles_by_status("scraped")
        assert len(vehicles) == 1
        assert vehicles[0]["error_message"] is None

    def test_retry_no_failed(self, sample_vehicle_data):
        upsert_vehicle(sample_vehicle_data)
        count = retry_failed_vehicles()
        assert count == 0

    def test_retry_custom_target_status(self, sample_vehicle_data):
        vid = upsert_vehicle(sample_vehicle_data)
        update_vehicle_status(vid, "error", error_message="Script fail")

        count = retry_failed_vehicles(target_status="script_generated")
        assert count == 1

        vehicles = get_vehicles_by_status("script_generated")
        assert len(vehicles) == 1

    def test_retry_single_vehicle(self, sample_vehicle_data):
        vid1 = upsert_vehicle(sample_vehicle_data)
        update_vehicle_status(vid1, "error", error_message="Fail 1")

        v2 = sample_vehicle_data.copy()
        v2["cargurus_id"] = "cg_67890"
        vid2 = upsert_vehicle(v2)
        update_vehicle_status(vid2, "error", error_message="Fail 2")

        # Only retry one
        result = retry_vehicle_by_id(vid1)
        assert result is True

        # vid1 should be reset, vid2 still error
        assert len(get_vehicles_by_status("scraped")) == 1
        assert len(get_vehicles_by_status("error")) == 1

    def test_retry_non_error_vehicle_returns_false(self, sample_vehicle_data):
        vid = upsert_vehicle(sample_vehicle_data)
        result = retry_vehicle_by_id(vid)
        assert result is False
