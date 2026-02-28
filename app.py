"""Flask web application for the CarGurus Video Pipeline dashboard.

Provides a web interface to view pipeline status, browse vehicles,
and trigger pipeline runs.
"""

import json
import os
import subprocess
import threading
from datetime import datetime

from flask import Flask, render_template, jsonify, request, send_from_directory

from config import settings
from utils.database import (
    init_db, get_all_vehicles, get_vehicles_by_status,
    get_pipeline_stats,
)
from utils.cost_tracker import CostTracker

app = Flask(__name__)

# Initialize database on startup
init_db()

# Track background pipeline runs
_pipeline_lock = threading.Lock()
_pipeline_running = False
_pipeline_log = []


@app.route("/")
def dashboard():
    """Main dashboard page."""
    stats = get_pipeline_stats()
    vehicles = get_all_vehicles()
    return render_template(
        "dashboard.html",
        stats=stats,
        vehicles=vehicles,
        dealer_name=settings.DEALER_NAME,
        settings=settings,
    )


@app.route("/api/stats")
def api_stats():
    """API endpoint for pipeline statistics."""
    stats = get_pipeline_stats()
    return jsonify(stats)


@app.route("/api/vehicles")
def api_vehicles():
    """API endpoint for vehicle listing."""
    status_filter = request.args.get("status")
    if status_filter:
        vehicles = get_vehicles_by_status(status_filter)
    else:
        vehicles = get_all_vehicles()
    return jsonify(vehicles)


@app.route("/api/vehicle/<int:vehicle_id>")
def api_vehicle_detail(vehicle_id):
    """API endpoint for single vehicle details."""
    vehicles = get_all_vehicles()
    vehicle = next((v for v in vehicles if v["id"] == vehicle_id), None)
    if not vehicle:
        return jsonify({"error": "Vehicle not found"}), 404

    # Parse JSON fields for the response
    if vehicle.get("photo_paths"):
        vehicle["photo_paths_list"] = json.loads(vehicle["photo_paths"])
    if vehicle.get("video_script"):
        vehicle["script_parsed"] = json.loads(vehicle["video_script"])

    return jsonify(vehicle)


@app.route("/api/run", methods=["POST"])
def api_run_pipeline():
    """Trigger a pipeline run in the background."""
    global _pipeline_running

    with _pipeline_lock:
        if _pipeline_running:
            return jsonify({"error": "Pipeline is already running"}), 409
        _pipeline_running = True

    step = request.json.get("step", "all") if request.is_json else "all"
    max_vehicles = request.json.get("max_vehicles", 0) if request.is_json else 0

    def run_in_background():
        global _pipeline_running
        try:
            cmd = ["python", "main.py", "--step", step]
            if max_vehicles > 0:
                cmd.extend(["--max", str(max_vehicles)])
            subprocess.run(cmd, cwd=str(settings.PROJECT_ROOT), timeout=3600)
        finally:
            with _pipeline_lock:
                _pipeline_running = False

    thread = threading.Thread(target=run_in_background, daemon=True)
    thread.start()

    return jsonify({"status": "started", "step": step})


@app.route("/api/status")
def api_pipeline_status():
    """Check if a pipeline run is in progress."""
    return jsonify({"running": _pipeline_running})


@app.route("/videos/<path:filename>")
def serve_video(filename):
    """Serve generated video files."""
    return send_from_directory(str(settings.VIDEOS_DIR), filename)


@app.route("/photos/<path:filename>")
def serve_photo(filename):
    """Serve downloaded vehicle photos."""
    return send_from_directory(str(settings.PHOTOS_DIR), filename)


@app.route("/health")
def health_check():
    """Health check endpoint for Cloud Run."""
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
