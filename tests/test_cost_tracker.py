"""Tests for the cost tracker module."""

from unittest.mock import patch

from utils.cost_tracker import CostTracker
from utils.database import upsert_vehicle, log_cost


class TestCostTracker:
    def test_initial_state(self):
        tracker = CostTracker()
        assert tracker.session_cost == 0.0

    def test_record_cost(self, sample_vehicle_data):
        vid = upsert_vehicle(sample_vehicle_data)
        tracker = CostTracker()

        tracker.record_cost(vid, "veo", "fast", 16.0, 2.40, "video_generation")
        assert tracker.session_cost == 2.40

    def test_remaining_budget(self):
        tracker = CostTracker()
        with patch("config.settings.COST_LIMIT", 50.0):
            tracker.session_cost = 10.0
            assert tracker.remaining_budget == 40.0

    def test_can_afford_within_budget(self):
        tracker = CostTracker()
        tracker.session_cost = 0.0
        assert tracker.can_afford("veo", "fast") is True

    def test_can_afford_exceeds_budget(self):
        tracker = CostTracker()
        with patch("config.settings.COST_LIMIT", 1.0):
            tracker.session_cost = 0.5
            # Veo fast at $0.15/sec * 16 sec = $2.40 per video
            assert tracker.can_afford("veo", "fast") is False

    def test_get_best_engine_primary(self):
        tracker = CostTracker()
        tracker.session_cost = 0.0
        engine, quality = tracker.get_best_engine()
        assert engine is not None

    def test_get_best_engine_exhausted(self):
        tracker = CostTracker()
        with patch("config.settings.COST_LIMIT", 0.0):
            engine, quality = tracker.get_best_engine()
            assert engine is None
            assert quality is None

    def test_total_spend_from_db(self, sample_vehicle_data):
        vid = upsert_vehicle(sample_vehicle_data)
        log_cost(vid, "veo", "fast", 16.0, 2.40, "video_generation")
        log_cost(vid, "gemini", "flash", 0, 0.001, "script_generation")

        tracker = CostTracker()
        assert abs(tracker.total_spend - 2.401) < 0.001

    def test_print_summary_no_error(self, sample_vehicle_data):
        """Ensure print_summary runs without crashing."""
        vid = upsert_vehicle(sample_vehicle_data)
        log_cost(vid, "veo", "fast", 16.0, 2.40, "video_generation")

        tracker = CostTracker()
        tracker.session_cost = 2.40
        tracker.print_summary()  # Should not raise
