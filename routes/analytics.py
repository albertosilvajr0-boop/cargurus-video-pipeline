"""Video analytics and A/B testing API routes.

Tracks video views, shares, and engagement.
Supports A/B testing by generating multiple video variants
and tracking which performs better.
"""

import json
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

from utils.database import get_connection
from utils.logger import get_logger

logger = get_logger("routes.analytics")

analytics_bp = Blueprint("analytics", __name__)


# --- Video Analytics ---

@analytics_bp.route("/api/analytics/overview")
def api_analytics_overview():
    """Get high-level analytics overview."""
    conn = get_connection()
    try:
        # Total videos
        cursor = conn.execute("SELECT COUNT(*) as count FROM vehicles WHERE video_path IS NOT NULL")
        total_videos = cursor.fetchone()["count"]

        # Total shares
        try:
            cursor = conn.execute("SELECT COUNT(*) as count FROM share_events")
            total_shares = cursor.fetchone()["count"]
        except Exception:
            total_shares = 0

        # Total views
        try:
            cursor = conn.execute("SELECT COALESCE(SUM(view_count), 0) as total FROM video_analytics")
            total_views = cursor.fetchone()["total"]
        except Exception:
            total_views = 0

        # Videos by status
        cursor = conn.execute(
            "SELECT status, COUNT(*) as count FROM vehicles GROUP BY status"
        )
        by_status = {row["status"]: row["count"] for row in cursor.fetchall()}

        # Top shared vehicles
        try:
            cursor = conn.execute("""
                SELECT v.id, v.year, v.make, v.model, v.trim,
                       COUNT(se.id) as share_count
                FROM vehicles v
                JOIN share_events se ON v.id = se.vehicle_id
                GROUP BY v.id
                ORDER BY share_count DESC
                LIMIT 10
            """)
            top_shared = [dict(row) for row in cursor.fetchall()]
        except Exception:
            top_shared = []

        # Recent shares
        try:
            cursor = conn.execute("""
                SELECT se.*, v.year, v.make, v.model
                FROM share_events se
                JOIN vehicles v ON se.vehicle_id = v.id
                ORDER BY se.created_at DESC
                LIMIT 20
            """)
            recent_shares = [dict(row) for row in cursor.fetchall()]
        except Exception:
            recent_shares = []

        # Cost analytics
        cursor = conn.execute("SELECT COALESCE(SUM(video_cost), 0) as total FROM vehicles")
        total_cost = cursor.fetchone()["total"]

    finally:
        conn.close()

    return jsonify({
        "total_videos": total_videos,
        "total_views": total_views,
        "total_shares": total_shares,
        "total_cost": total_cost,
        "cost_per_video": round(total_cost / total_videos, 2) if total_videos > 0 else 0,
        "by_status": by_status,
        "top_shared": top_shared,
        "recent_shares": recent_shares,
    })


@analytics_bp.route("/api/analytics/vehicle/<int:vehicle_id>")
def api_analytics_vehicle(vehicle_id):
    """Get analytics for a specific vehicle."""
    conn = get_connection()
    try:
        # Vehicle info
        cursor = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,))
        vehicle = cursor.fetchone()
        if not vehicle:
            return jsonify({"error": "Vehicle not found"}), 404

        # Share events
        try:
            cursor = conn.execute(
                "SELECT platform, COUNT(*) as count FROM share_events "
                "WHERE vehicle_id = ? GROUP BY platform",
                (vehicle_id,),
            )
            shares_by_platform = {row["platform"]: row["count"] for row in cursor.fetchall()}
        except Exception:
            shares_by_platform = {}

        # View count
        try:
            cursor = conn.execute(
                "SELECT COALESCE(SUM(view_count), 0) as views FROM video_analytics WHERE vehicle_id = ?",
                (vehicle_id,),
            )
            views = cursor.fetchone()["views"]
        except Exception:
            views = 0

        # A/B test variants
        try:
            cursor = conn.execute(
                "SELECT * FROM ab_test_variants WHERE vehicle_id = ? ORDER BY created_at",
                (vehicle_id,),
            )
            variants = [dict(row) for row in cursor.fetchall()]
        except Exception:
            variants = []

    finally:
        conn.close()

    return jsonify({
        "vehicle_id": vehicle_id,
        "views": views,
        "shares_by_platform": shares_by_platform,
        "total_shares": sum(shares_by_platform.values()),
        "ab_variants": variants,
    })


@analytics_bp.route("/api/analytics/track-view", methods=["POST"])
def api_track_view():
    """Track a video view event."""
    data = request.get_json()
    if not data or not data.get("vehicle_id"):
        return jsonify({"error": "vehicle_id is required"}), 400

    vehicle_id = int(data["vehicle_id"])
    source = data.get("source", "direct")  # direct, social, embed, email

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO video_analytics (vehicle_id, view_count, source, created_at) "
            "VALUES (?, 1, ?, ?)",
            (vehicle_id, source, datetime.now().isoformat()),
        )
        conn.commit()
    except Exception as e:
        logger.debug("Failed to track view: %s", e)
    finally:
        conn.close()

    return jsonify({"status": "tracked"})


# --- A/B Testing ---

@analytics_bp.route("/api/ab-test/create", methods=["POST"])
def api_ab_test_create():
    """Create an A/B test for a vehicle.

    Generates two video variants with different styles/prompts
    and tracks which one gets more engagement.

    JSON body:
        vehicle_id: int
        variant_a_template_id: int (prompt template for variant A)
        variant_b_template_id: int (prompt template for variant B)
    """
    data = request.get_json()
    if not data or not data.get("vehicle_id"):
        return jsonify({"error": "vehicle_id is required"}), 400

    vehicle_id = int(data["vehicle_id"])
    test_id = f"ab_{uuid.uuid4().hex[:12]}"

    conn = get_connection()
    try:
        # Verify vehicle exists
        cursor = conn.execute("SELECT id FROM vehicles WHERE id = ?", (vehicle_id,))
        if not cursor.fetchone():
            return jsonify({"error": "Vehicle not found"}), 404

        # Create A/B test record
        conn.execute(
            "INSERT INTO ab_tests (test_id, vehicle_id, status, created_at) VALUES (?, ?, 'pending', ?)",
            (test_id, vehicle_id, datetime.now().isoformat()),
        )

        # Create variant records
        for variant_label in ["A", "B"]:
            template_id = data.get(f"variant_{variant_label.lower()}_template_id")
            conn.execute(
                "INSERT INTO ab_test_variants (test_id, vehicle_id, variant_label, "
                "prompt_template_id, views, shares, created_at) VALUES (?, ?, ?, ?, 0, 0, ?)",
                (test_id, vehicle_id, variant_label, template_id, datetime.now().isoformat()),
            )

        conn.commit()
    except Exception as e:
        logger.error("Failed to create A/B test: %s", e)
        conn.close()
        return jsonify({"error": f"Failed to create test: {e}"}), 500
    finally:
        conn.close()

    return jsonify({
        "test_id": test_id,
        "vehicle_id": vehicle_id,
        "status": "pending",
        "message": "A/B test created. Generate videos for each variant to start the test.",
    })


@analytics_bp.route("/api/ab-test/<test_id>")
def api_ab_test_status(test_id):
    """Get A/B test results."""
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT * FROM ab_tests WHERE test_id = ?", (test_id,))
        test = cursor.fetchone()
        if not test:
            return jsonify({"error": "Test not found"}), 404

        cursor = conn.execute(
            "SELECT * FROM ab_test_variants WHERE test_id = ? ORDER BY variant_label",
            (test_id,),
        )
        variants = [dict(row) for row in cursor.fetchall()]

        # Determine winner
        winner = None
        if len(variants) == 2:
            a_score = (variants[0].get("views", 0) * 1) + (variants[0].get("shares", 0) * 5)
            b_score = (variants[1].get("views", 0) * 1) + (variants[1].get("shares", 0) * 5)
            if a_score > b_score:
                winner = "A"
            elif b_score > a_score:
                winner = "B"

    finally:
        conn.close()

    return jsonify({
        "test_id": test_id,
        "test": dict(test),
        "variants": variants,
        "winner": winner,
    })


@analytics_bp.route("/api/ab-tests")
def api_ab_test_list():
    """List all A/B tests."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT at_tests.*, v.year, v.make, v.model "
            "FROM ab_tests at_tests "
            "JOIN vehicles v ON at_tests.vehicle_id = v.id "
            "ORDER BY at_tests.created_at DESC"
        )
        tests = [dict(row) for row in cursor.fetchall()]
    except Exception:
        tests = []
    finally:
        conn.close()

    return jsonify(tests)
