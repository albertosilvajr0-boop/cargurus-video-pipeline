"""Flask web application for the upload-first vehicle video pipeline.

This module handles application startup, data restoration, and blueprint registration.
Route handlers live in routes/, pipeline logic lives in workers/.
"""

import os
import secrets
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, jsonify, send_from_directory, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import settings
from utils.logger import get_logger

logger = get_logger("app")

app = Flask(__name__)

# --- Security configuration ---
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

# --- Rate limiting ---
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per minute"],
    storage_uri="memory://",
)

# --- Security headers ---
@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# --- Database initialization ---
from utils.database import (
    init_db, get_all_vehicles, get_pipeline_stats,
    seed_default_templates, get_branding_settings, get_connection,
)

init_db()

# --- Restore persisted data from Firestore/JSON backups ---
from utils.data_persistence import restore_all, _get_firestore, export_media_library
_fs_client = _get_firestore()
if _fs_client:
    logger.info("Firestore connected — data will persist across container restarts")
else:
    logger.warning("Firestore NOT available — data will be LOST on container restart! "
                    "Enable the Firestore API and ensure the service account has datastore.user role.")
restored = restore_all()
if restored:
    logger.info("Restored session data from persistent backup")

seed_default_templates()
logger.info("Application starting — PRIMARY_VIDEO_ENGINE=%s", settings.PRIMARY_VIDEO_ENGINE)

# --- Restore branding from database ---
_saved_branding = get_branding_settings()
if _saved_branding:
    if _saved_branding.get("dealer_name"):
        settings.DEALER_NAME = _saved_branding["dealer_name"]
    if _saved_branding.get("dealer_phone"):
        settings.DEALER_PHONE = _saved_branding["dealer_phone"]
    if _saved_branding.get("dealer_address"):
        settings.DEALER_ADDRESS = _saved_branding["dealer_address"]
    if _saved_branding.get("dealer_website"):
        settings.DEALER_WEBSITE = _saved_branding["dealer_website"]
    if _saved_branding.get("dealer_logo_path"):
        _logo_path = _saved_branding["dealer_logo_path"]
        if Path(_logo_path).exists():
            settings.DEALER_LOGO_PATH = _logo_path
        else:
            from utils.cloud_storage import is_gcs_enabled, download_branding_asset
            if is_gcs_enabled():
                _logo_blob = f"branding/{Path(_logo_path).name}"
                if download_branding_asset(_logo_blob, _logo_path):
                    settings.DEALER_LOGO_PATH = _logo_path
                    logger.info("Restored dealer logo from GCS: %s", _logo_path)
                else:
                    logger.warning("Dealer logo not found locally or in GCS: %s", _logo_path)

# --- Restore uploaded files from GCS after cold restart ---
from utils.cloud_storage import (
    is_gcs_enabled, download_branding_asset,
    download_directory as gcs_download_directory,
    list_prefixes as gcs_list_prefixes,
)

if is_gcs_enabled():
    _upload_prefixes = gcs_list_prefixes("uploads/")
    _restored_uploads = 0
    for prefix in _upload_prefixes:
        _upload_id = prefix.strip("/").split("/")[-1]
        _local_upload_dir = settings.UPLOADS_DIR / _upload_id
        if not _local_upload_dir.exists() or not any(_local_upload_dir.iterdir()):
            count = gcs_download_directory(prefix.rstrip("/"), str(_local_upload_dir))
            if count:
                _restored_uploads += count
    if _restored_uploads:
        logger.info("Restored %d uploaded photo files from GCS", _restored_uploads)

    _media_prefixes = gcs_list_prefixes("media/")
    _restored_media = 0
    for prefix in _media_prefixes:
        _group_name = prefix.strip("/").split("/")[-1]
        _local_media_dir = settings.MEDIA_DIR / _group_name
        if not _local_media_dir.exists() or not any(_local_media_dir.iterdir()):
            count = gcs_download_directory(prefix.rstrip("/"), str(_local_media_dir))
            if count:
                _restored_media += count
    if _restored_media:
        logger.info("Restored %d media library files from GCS", _restored_media)

    _people_prefixes = gcs_list_prefixes("people/")
    _restored_people = 0
    for prefix in _people_prefixes:
        count = gcs_download_directory(prefix.rstrip("/"), str(settings.PEOPLE_DIR))
        if count:
            _restored_people += count
    if _restored_people:
        logger.info("Restored %d people photo files from GCS", _restored_people)

# --- Shared job tracking (Firestore-backed, survives restarts) ---
from utils.job_store import get_job_store
_job_store = get_job_store()
# Legacy compatibility: keep _jobs_lock and _active_jobs for existing code
_jobs_lock = _job_store.lock
_active_jobs = _job_store._jobs

# --- Register route blueprints ---
from routes.upload import upload_bp, init_routes as init_upload_routes
from routes.vehicles import vehicles_bp, init_routes as init_vehicle_routes
from routes.media import media_bp
from routes.events import events_bp
from routes.batch import batch_bp
from routes.social import social_bp
from routes.analytics import analytics_bp
from routes.integrations import integrations_bp

init_upload_routes(_jobs_lock, _active_jobs)
init_vehicle_routes(_jobs_lock, _active_jobs)

app.register_blueprint(upload_bp)
app.register_blueprint(vehicles_bp)
app.register_blueprint(media_bp)
app.register_blueprint(events_bp)
app.register_blueprint(batch_bp)
app.register_blueprint(social_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(integrations_bp)

# --- Auth routes ---
from utils.auth import register_auth_routes
register_auth_routes(app)


# --- Error handlers ---
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(429)
def rate_limited(e):
    return jsonify({"error": "Rate limit exceeded. Please slow down.", "retry_after": e.description}), 429


@app.errorhandler(413)
def request_too_large(e):
    return jsonify({"error": f"Upload too large. Maximum: {settings.MAX_UPLOAD_SIZE_MB}MB"}), 413


@app.errorhandler(500)
def internal_error(e):
    logger.error("Internal server error: %s", e)
    return jsonify({"error": "Internal server error"}), 500


# --- Pages ---

@app.route("/")
def dashboard():
    stats = get_pipeline_stats()
    vehicles = get_all_vehicles()
    return render_template(
        "dashboard.html",
        stats=stats,
        vehicles=vehicles,
        dealer_name=settings.DEALER_NAME,
        dealer_phone=settings.DEALER_PHONE,
        dealer_address=settings.DEALER_ADDRESS,
        settings=settings,
    )


# --- Static file serving ---

@app.route("/videos/<path:filename>")
def serve_video(filename):
    return send_from_directory(str(settings.VIDEOS_DIR), filename)


@app.route("/photos/<path:filename>")
def serve_photo(filename):
    return send_from_directory(str(settings.PHOTOS_DIR), filename)


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(str(settings.UPLOADS_DIR), filename)


@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(str(settings.PROJECT_ROOT / "public"), filename)


# --- Diagnostics ---

@app.route("/api/logs")
def api_recent_logs():
    from utils.logger import LOG_FILE
    max_lines = min(int(request.args.get("lines", 100)), 500)
    level_filter = request.args.get("level", "").upper()

    if not LOG_FILE.exists():
        return jsonify({"lines": [], "message": "No log file yet"})

    with open(LOG_FILE, encoding="utf-8") as f:
        all_lines = f.readlines()

    if level_filter:
        all_lines = [l for l in all_lines if f"| {level_filter}" in l]

    recent = all_lines[-max_lines:]
    return jsonify({"lines": [l.rstrip() for l in recent], "total": len(all_lines)})


@app.route("/api/gcs/status")
def api_gcs_status():
    result = {
        "enabled": is_gcs_enabled(),
        "bucket": settings.GCS_BUCKET_NAME or None,
        "credentials_path": settings.GCS_CREDENTIALS_PATH or None,
        "public_url_base": settings.GCS_PUBLIC_URL_BASE or None,
    }
    if is_gcs_enabled():
        try:
            from utils.cloud_storage import _get_client
            client = _get_client()
            bucket = client.bucket(settings.GCS_BUCKET_NAME)
            result["bucket_exists"] = bucket.exists()
            result["reachable"] = True
        except Exception as e:
            result["reachable"] = False
            result["error"] = f"{type(e).__name__}: {e}"
    return jsonify(result)


@app.route("/api/persistence-status")
def api_persistence_status():
    from utils.data_persistence import _get_firestore, _firestore_available, _load_from_firestore
    from utils.data_persistence import FS_TEMPLATES_DOC, FS_VEHICLES_DOC, FS_BRANDING_DOC, FS_PEOPLE_DOC

    result = {
        "firestore_available": _firestore_available,
        "sqlite_counts": {},
        "firestore_counts": {},
    }

    try:
        conn = get_connection()
        for table in ["prompt_templates", "vehicles", "branding_settings", "people", "people_photos"]:
            cursor = conn.execute(f"SELECT COUNT(*) as count FROM {table}")
            result["sqlite_counts"][table] = cursor.fetchone()["count"]
        conn.close()
    except Exception as e:
        result["sqlite_error"] = str(e)

    client = _get_firestore()
    if client:
        result["firestore_available"] = True
        for doc_name, label in [(FS_TEMPLATES_DOC, "prompt_templates"),
                                 (FS_VEHICLES_DOC, "vehicles"),
                                 (FS_BRANDING_DOC, "branding"),
                                 (FS_PEOPLE_DOC, "people")]:
            data = _load_from_firestore(doc_name)
            if data:
                result["firestore_counts"][label] = len(data) if isinstance(data, list) else 1
            else:
                result["firestore_counts"][label] = 0
    else:
        result["firestore_available"] = False
        result["firestore_error"] = "Could not initialize Firestore client."

    return jsonify(result)


@app.route("/health")
@limiter.exempt
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
