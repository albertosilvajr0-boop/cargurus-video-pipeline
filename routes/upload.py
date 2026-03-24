"""Upload and video generation API routes."""

import threading
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

from config import settings
from utils.database import (
    get_prompt_template, get_photos_for_person,
    get_media_items_by_ids,
)
from utils.cloud_storage import is_gcs_enabled
from utils.data_persistence import export_media_library
from workers.pipeline import run_upload_pipeline, run_vin_pipeline
from utils.vin_decoder import validate_vin, decode_vin
from utils.logger import get_logger

logger = get_logger("routes.upload")

upload_bp = Blueprint("upload", __name__)

# Shared job tracking — injected from app.py via init_routes()
_jobs_lock: threading.Lock = None
_active_jobs: dict = None

# Upload validation constants
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".pdf"}
MAX_FILE_SIZE_MB = 20
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024


def init_routes(jobs_lock: threading.Lock, active_jobs: dict):
    """Wire up shared job state from the app."""
    global _jobs_lock, _active_jobs
    _jobs_lock = jobs_lock
    _active_jobs = active_jobs


def _resolve_person_photo(person_option: str | None) -> str | None:
    """Resolve a person_option value to a file path."""
    if not person_option or person_option == "ai":
        return None
    try:
        photos = get_photos_for_person(int(person_option))
        for ph in photos:
            if Path(ph["file_path"]).exists():
                return ph["file_path"]
    except (ValueError, TypeError):
        pass
    return None


def _validate_upload_file(file) -> str | None:
    """Validate an uploaded file. Returns error message or None if valid."""
    if not file or not file.filename:
        return None  # skip empty files silently
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}"
    # Check content length if available
    file.seek(0, 2)  # seek to end
    size = file.tell()
    file.seek(0)  # reset
    if size > MAX_FILE_SIZE_BYTES:
        return f"File too large ({size // (1024*1024)}MB). Maximum: {MAX_FILE_SIZE_MB}MB"
    return None


@upload_bp.route("/api/upload", methods=["POST"])
def api_upload_vehicle():
    """Upload vehicle photos + optional sticker/carfax, kick off pipeline."""
    from datetime import datetime

    photos = request.files.getlist("photos[]")
    if not photos or all(f.filename == "" for f in photos):
        return jsonify({"error": "At least one vehicle photo is required"}), 400

    # Validate all files before saving
    for photo in photos:
        err = _validate_upload_file(photo)
        if err:
            return jsonify({"error": err}), 400

    for field_name in ["sticker", "carfax"]:
        f = request.files.get(field_name)
        if f and f.filename:
            err = _validate_upload_file(f)
            if err:
                return jsonify({"error": err}), 400

    upload_id = f"upload_{uuid.uuid4().hex[:12]}"
    upload_dir = settings.UPLOADS_DIR / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    photo_paths = []

    for i, photo in enumerate(photos):
        if photo.filename == "":
            continue
        ext = Path(photo.filename).suffix.lower() or ".jpg"
        filename = f"photo_{i:02d}{ext}"
        filepath = upload_dir / filename
        photo.save(str(filepath))
        saved_paths.append(str(filepath))
        photo_paths.append(str(filepath))

    sticker = request.files.get("sticker")
    sticker_path = None
    if sticker and sticker.filename:
        ext = Path(sticker.filename).suffix.lower() or ".jpg"
        sticker_path = str(upload_dir / f"sticker{ext}")
        sticker.save(sticker_path)
        saved_paths.append(sticker_path)

    carfax = request.files.get("carfax")
    carfax_path = None
    if carfax and carfax.filename:
        ext = Path(carfax.filename).suffix.lower() or ".jpg"
        carfax_path = str(upload_dir / f"carfax{ext}")
        carfax.save(carfax_path)
        saved_paths.append(carfax_path)

    job_id = upload_id
    with _jobs_lock:
        _active_jobs[job_id] = {
            "status": "extracting",
            "progress": "Analyzing images with Gemini...",
            "upload_id": upload_id,
            "vehicle_id": None,
            "started_at": datetime.now().isoformat(),
        }

    overrides = {
        "dealer_phone": request.form.get("dealer_phone", ""),
        "dealer_address": request.form.get("dealer_address", ""),
        "cta_text": request.form.get("cta_text", ""),
    }

    person_photo_path = _resolve_person_photo(request.form.get("person_option", "ai"))

    prompt_template_id = request.form.get("prompt_template_id")
    prompt_template = get_prompt_template(int(prompt_template_id)) if prompt_template_id else None

    thread = threading.Thread(
        target=run_upload_pipeline,
        args=(job_id, upload_id, saved_paths, photo_paths, sticker_path, overrides),
        kwargs=dict(
            prompt_template=prompt_template,
            prompt_template_id=prompt_template_id,
            person_photo_path=person_photo_path,
            carfax_path=carfax_path,
            jobs_lock=_jobs_lock,
            active_jobs=_active_jobs,
        ),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "upload_id": upload_id, "status": "processing"})


@upload_bp.route("/api/vin", methods=["POST"])
def api_vin_generate():
    """Generate a video from just a VIN number."""
    from datetime import datetime

    data = request.get_json()
    if not data or not data.get("vin"):
        return jsonify({"error": "VIN is required"}), 400

    raw_vin = data["vin"]
    clean_vin = validate_vin(raw_vin)
    if not clean_vin:
        return jsonify({"error": f"Invalid VIN: {raw_vin}. Must be 17 alphanumeric characters (no I, O, Q)."}), 400

    job_id = f"vin_{clean_vin}_{uuid.uuid4().hex[:6]}"

    with _jobs_lock:
        _active_jobs[job_id] = {
            "status": "decoding",
            "progress": f"Decoding VIN {clean_vin}...",
            "vin": clean_vin,
            "vehicle_id": None,
            "started_at": datetime.now().isoformat(),
        }

    overrides = {
        "price": data.get("price"),
        "dealer_phone": data.get("dealer_phone", ""),
        "dealer_address": data.get("dealer_address", ""),
        "cta_text": data.get("cta_text", ""),
    }

    person_photo_path = _resolve_person_photo(data.get("person_option", "ai"))

    vin_prompt_template_id = data.get("prompt_template_id")
    prompt_template = get_prompt_template(int(vin_prompt_template_id)) if vin_prompt_template_id else None

    thread = threading.Thread(
        target=run_vin_pipeline,
        args=(job_id, clean_vin, overrides),
        kwargs=dict(
            prompt_template=prompt_template,
            prompt_template_id=vin_prompt_template_id,
            person_photo_path=person_photo_path,
            jobs_lock=_jobs_lock,
            active_jobs=_active_jobs,
        ),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "vin": clean_vin, "status": "processing"})


@upload_bp.route("/api/vin/decode", methods=["POST"])
def api_vin_decode_only():
    """Quick VIN decode — returns vehicle specs without generating a video."""
    data = request.get_json()
    if not data or not data.get("vin"):
        return jsonify({"error": "VIN is required"}), 400

    clean_vin = validate_vin(data["vin"])
    if not clean_vin:
        return jsonify({"error": "Invalid VIN"}), 400

    specs = decode_vin(clean_vin)
    if not specs:
        return jsonify({"error": "Could not decode VIN"}), 422

    return jsonify(specs)


@upload_bp.route("/api/media/generate", methods=["POST"])
def api_media_generate_video():
    """Generate a video from saved media library items."""
    from datetime import datetime

    data = request.get_json()
    if not data or not data.get("media_ids"):
        return jsonify({"error": "media_ids list is required"}), 400

    media_ids = data["media_ids"]
    items = get_media_items_by_ids(media_ids)
    if not items:
        return jsonify({"error": "No media items found for the given IDs"}), 404

    photo_paths = [i["file_path"] for i in items if i["file_type"] == "photo"]
    sticker_items = [i for i in items if i["file_type"] == "sticker"]
    sticker_path = sticker_items[0]["file_path"] if sticker_items else None
    carfax_items = [i for i in items if i["file_type"] == "carfax"]
    carfax_path = carfax_items[0]["file_path"] if carfax_items else None
    all_image_paths = [i["file_path"] for i in items]

    if not photo_paths:
        return jsonify({"error": "At least one photo is required in the selected media"}), 400

    upload_id = f"media_{uuid.uuid4().hex[:12]}"
    job_id = upload_id

    with _jobs_lock:
        _active_jobs[job_id] = {
            "status": "extracting",
            "progress": "Analyzing saved media with Gemini...",
            "upload_id": upload_id,
            "vehicle_id": None,
            "started_at": datetime.now().isoformat(),
        }

    overrides = {
        "dealer_phone": data.get("dealer_phone", ""),
        "dealer_address": data.get("dealer_address", ""),
        "cta_text": data.get("cta_text", ""),
    }

    prompt_template_id = data.get("prompt_template_id")
    prompt_template = get_prompt_template(int(prompt_template_id)) if prompt_template_id else None
    person_photo_path = _resolve_person_photo(data.get("person_option", "ai"))

    thread = threading.Thread(
        target=run_upload_pipeline,
        args=(job_id, upload_id, all_image_paths, photo_paths, sticker_path, overrides),
        kwargs=dict(
            prompt_template=prompt_template,
            prompt_template_id=prompt_template_id,
            person_photo_path=person_photo_path,
            carfax_path=carfax_path,
            jobs_lock=_jobs_lock,
            active_jobs=_active_jobs,
        ),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "upload_id": upload_id, "status": "processing"})
