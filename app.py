"""Flask web application for the CarGurus Video Pipeline dashboard.

Provides a web interface to view pipeline status, browse vehicles,
and trigger pipeline runs.
"""

import csv
import io
import json
import os
import re
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


@app.route("/api/upload-csv", methods=["POST"])
def api_upload_csv():
    """Import vehicles from a bookmarklet-generated CSV file."""
    from utils.database import upsert_vehicle

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename.endswith(".csv"):
        return jsonify({"error": "File must be a CSV"}), 400

    try:
        content = file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))

        imported = 0
        skipped = 0
        photo_total = 0

        def parse_num(val):
            if not val:
                return 0
            cleaned = val.replace("$", "").replace(",", "").replace('"', "").strip()
            try:
                return int(float(cleaned)) if cleaned else 0
            except ValueError:
                return 0

        def clean_trim(raw_trim):
            """Strip mileage, location, and deal rating that bleeds into trim."""
            t = (raw_trim or "").strip().strip('"')
            # Remove patterns like "7,726 miSan Antonio, TXGood Deal"
            t = re.sub(r"[\d,]+\s*mi[A-Z].*$", "", t)
            # Remove standalone deal ratings
            t = re.sub(r"(Great|Good|Fair|High|No)\s+Deal.*$", "", t, flags=re.I)
            # Remove city/state patterns
            t = re.sub(r"[A-Z][a-z]+,\s*[A-Z]{2}.*$", "", t)
            return t.strip()

        for row in reader:
            vin = (row.get("VIN") or "").strip()
            if not vin or len(vin) != 17:
                skipped += 1
                continue

            # Parse photo URLs (pipe-separated from bookmarklet)
            photos_raw = (row.get("Photos") or "").strip().strip('"')
            photo_urls = [u.strip() for u in photos_raw.split("|") if u.strip()] if photos_raw else []
            photo_total += len(photo_urls)

            vehicle_data = {
                "cargurus_id": f"csv_{vin}",
                "vin": vin,
                "year": parse_num(row.get("Year")),
                "make": (row.get("Make") or "").strip().strip('"'),
                "model": (row.get("Model") or "").strip().strip('"'),
                "trim": clean_trim(row.get("Trim")),
                "price": parse_num(row.get("Sale Price")),
                "mileage": parse_num(row.get("Mileage")),
                "exterior_color": (row.get("Color") or "").strip().strip('"'),
                "drivetrain": (row.get("Drivetrain") or "").strip().strip('"'),
                "listing_url": (row.get("URL") or "").strip().strip('"'),
                "photo_urls": json.dumps(photo_urls),
                "status": "scraped",
            }

            upsert_vehicle(vehicle_data)
            imported += 1

        return jsonify({
            "status": "success",
            "imported": imported,
            "skipped": skipped,
            "photos": photo_total,
        })

    except Exception as e:
        return jsonify({"error": f"Failed to parse CSV: {str(e)}"}), 400


@app.route("/api/reset", methods=["POST"])
def api_reset_vehicles():
    """Delete all vehicles so the user can re-import a fresh CSV."""
    from utils.database import get_connection
    conn = get_connection()
    conn.execute("DELETE FROM vehicles")
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


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
