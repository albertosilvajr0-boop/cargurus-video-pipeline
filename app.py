"""Flask web application for the upload-first vehicle video pipeline.

New workflow:
  1. User uploads photos + window sticker + Carfax via drag-and-drop
  2. Gemini multimodal extracts vehicle details and generates script
  3. Sora generates a cinematic video clip
  4. FFmpeg composites intro + clip + CTA outro with overlays
  5. User previews and downloads the final branded video
"""

import asyncio
import json
import os
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, jsonify, request, send_from_directory

from config import settings
from utils.logger import get_logger

logger = get_logger("app")
from scripts.multimodal_extractor import MultimodalExtractor
from scripts.vin_script_generator import VINScriptGenerator
from utils.vin_decoder import decode_vin, validate_vin
from video_gen.sora_generator import SoraGenerator
from video_gen.overlay import VideoOverlayPipeline
from utils.database import (
    init_db, get_all_vehicles, get_vehicles_by_status,
    get_pipeline_stats, upsert_vehicle, update_vehicle_status,
    retry_failed_vehicles, retry_vehicle_by_id,
    seed_default_templates, get_all_prompt_templates, get_prompt_template,
    create_prompt_template, update_prompt_template, delete_prompt_template,
    save_branding_settings, get_branding_settings,
)
from utils.cost_tracker import CostTracker

app = Flask(__name__)

# Initialize database on startup
init_db()
seed_default_templates()
logger.info("Application starting — PRIMARY_VIDEO_ENGINE=%s", settings.PRIMARY_VIDEO_ENGINE)

# Restore branding from database (persists across deployments)
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
    if _saved_branding.get("dealer_logo_path") and Path(_saved_branding["dealer_logo_path"]).exists():
        settings.DEALER_LOGO_PATH = _saved_branding["dealer_logo_path"]

# Track background jobs
_jobs_lock = threading.Lock()
_active_jobs = {}  # job_id -> {"status": str, "vehicle_id": int, "progress": str, ...}

# --- Pages ---

@app.route("/")
def dashboard():
    """Main upload dashboard page."""
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


# --- Upload + Process API ---

@app.route("/api/upload", methods=["POST"])
def api_upload_vehicle():
    """
    Upload vehicle photos, window sticker, and Carfax.
    Saves files and kicks off the full pipeline in background.

    Form fields:
      - photos[]: multiple image files (required, at least 1)
      - sticker: window sticker image/PDF (optional)
      - carfax: Carfax report image/PDF (optional)
      - dealer_phone: override phone for this video (optional)
      - dealer_address: override address for this video (optional)
      - cta_text: custom CTA text (optional)
    """
    photos = request.files.getlist("photos[]")
    if not photos or all(f.filename == "" for f in photos):
        return jsonify({"error": "At least one vehicle photo is required"}), 400

    # Generate a unique ID for this upload batch
    upload_id = f"upload_{uuid.uuid4().hex[:12]}"
    upload_dir = settings.UPLOADS_DIR / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Save all uploaded files
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

    # Create a job and process in background
    job_id = upload_id
    with _jobs_lock:
        _active_jobs[job_id] = {
            "status": "extracting",
            "progress": "Analyzing images with Gemini...",
            "upload_id": upload_id,
            "vehicle_id": None,
            "started_at": datetime.now().isoformat(),
        }

    # Gather optional overrides
    overrides = {
        "dealer_phone": request.form.get("dealer_phone", ""),
        "dealer_address": request.form.get("dealer_address", ""),
        "cta_text": request.form.get("cta_text", ""),
    }

    # Optional prompt template
    prompt_template_id = request.form.get("prompt_template_id")
    prompt_template = None
    if prompt_template_id:
        prompt_template = get_prompt_template(int(prompt_template_id))

    thread = threading.Thread(
        target=_process_upload,
        args=(job_id, upload_id, saved_paths, photo_paths, sticker_path, overrides, prompt_template, prompt_template_id),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "upload_id": upload_id, "status": "processing"})


@app.route("/api/vin", methods=["POST"])
def api_vin_generate():
    """
    Generate a video from just a VIN number.

    JSON body:
      - vin: 17-character VIN (required)
      - price: vehicle price (optional)
      - dealer_phone: override phone (optional)
      - dealer_address: override address (optional)
      - cta_text: custom CTA text (optional)
    """
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

    # Optional prompt template
    vin_prompt_template_id = data.get("prompt_template_id")
    prompt_template = None
    if vin_prompt_template_id:
        prompt_template = get_prompt_template(int(vin_prompt_template_id))

    thread = threading.Thread(
        target=_process_vin,
        args=(job_id, clean_vin, overrides, prompt_template, vin_prompt_template_id),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "vin": clean_vin, "status": "processing"})


@app.route("/api/vin/decode", methods=["POST"])
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


def _process_vin(job_id: str, vin: str, overrides: dict, prompt_template: dict | None = None, prompt_template_id: str | None = None):
    """Background worker: decode VIN → generate script → generate video → overlay → done."""
    logger.info("=== VIN pipeline started — job=%s, vin=%s ===", job_id, vin)

    def update_job(**kwargs):
        with _jobs_lock:
            _active_jobs[job_id].update(kwargs)
        if "status" in kwargs:
            logger.info("Job %s status -> %s: %s", job_id, kwargs.get("status"), kwargs.get("progress", ""))

    try:
        # --- Step 1: Decode VIN via NHTSA ---
        update_job(status="decoding", progress=f"Decoding VIN {vin}...")
        specs = decode_vin(vin)

        if not specs:
            update_job(status="error", progress="Could not decode VIN — check the number and try again")
            return

        vehicle_name = specs.get("vehicle_name", vin)
        update_job(vehicle_name=vehicle_name, progress=f"Decoded: {vehicle_name}")

        # --- Step 2: Generate video script ---
        update_job(status="extracting", progress=f"Generating video script for {vehicle_name}...")
        price = overrides.get("price")
        generator = VINScriptGenerator()
        result = generator.generate(specs, price=price, prompt_template=prompt_template)

        if not result:
            update_job(status="error", progress="Failed to generate video script")
            return

        script_info = result.get("script", {})

        # Save vehicle to database
        upload_id = f"vin_{vin}"
        vehicle_data = {
            "cargurus_id": upload_id,
            "vin": vin,
            "year": specs.get("year") or 0,
            "make": specs.get("make", ""),
            "model": specs.get("model", ""),
            "trim": specs.get("trim", ""),
            "price": price or 0,
            "exterior_color": specs.get("exterior_color", ""),
            "engine": specs.get("engine", ""),
            "transmission": specs.get("transmission", ""),
            "drivetrain": specs.get("drivetrain", ""),
            "video_script": json.dumps(result),
            "status": "script_generated",
            "script_generated_at": datetime.now().isoformat(),
            "prompt_template_id": int(prompt_template_id) if prompt_template_id else None,
        }
        vehicle_id = upsert_vehicle(vehicle_data)
        update_job(vehicle_id=vehicle_id)

        # --- Step 3: Generate AI video clip ---
        update_job(status="generating", progress=f"Generating video for {vehicle_name}...")

        veo_prompt = script_info.get("veo_prompt", "")
        if not veo_prompt:
            update_job(status="error", progress="No video prompt generated")
            return

        sora = SoraGenerator()
        clip_path = asyncio.run(
            sora.generate_clip(veo_prompt, None, upload_id)
        )

        if not clip_path:
            sora_err = getattr(sora, "_last_error", None) or "unknown"
            update_job(status="error", progress=f"Sora video generation failed — {sora_err}")
            update_vehicle_status(vehicle_id, "error", error_message=f"Sora failed: {sora_err}")
            return

        # --- Step 4: Overlay pipeline (no hero photo — text-only intro) ---
        update_job(status="compositing", progress="Adding branding and overlays...")

        overlay = VideoOverlayPipeline()
        final_path = overlay.compose_final_video(
            ai_clip_path=clip_path,
            hero_photo_path=None,
            vehicle_name=vehicle_name,
            price=price,
            output_name=upload_id,
            dealer_phone=overrides.get("dealer_phone") or "",
            dealer_address=overrides.get("dealer_address") or "",
            dealer_logo_path=settings.DEALER_LOGO_PATH,
            cta_text=overrides.get("cta_text") or "",
            vehicle_specs=specs,
        )

        if not final_path:
            update_job(status="error", progress="Overlay compositing failed")
            update_vehicle_status(vehicle_id, "error", error_message="Overlay compositing failed")
            return

        # --- Done ---
        cost_tracker = CostTracker()
        update_vehicle_status(
            vehicle_id,
            "video_complete",
            video_path=final_path,
            video_engine="sora",
            video_cost=cost_tracker.session_cost,
            video_generated_at=datetime.now().isoformat(),
        )

        caption = script_info.get("caption", "")
        update_job(
            status="complete",
            progress="Video ready!",
            video_path=final_path,
            video_filename=Path(final_path).name,
            caption=caption,
        )

    except Exception as e:
        logger.error("VIN pipeline uncaught exception (job=%s): %s: %s", job_id, type(e).__name__, e)
        logger.debug("VIN pipeline traceback:\n%s", traceback.format_exc())
        update_job(status="error", progress=f"Pipeline error: {str(e)}")


def _process_upload(
    job_id: str,
    upload_id: str,
    all_image_paths: list[str],
    photo_paths: list[str],
    sticker_path: str | None,
    overrides: dict,
    prompt_template: dict | None = None,
    prompt_template_id: str | None = None,
):
    """Background worker: extract → generate video → overlay → done."""
    logger.info(
        "=== Upload pipeline started — job=%s, upload=%s, images=%d ===",
        job_id, upload_id, len(all_image_paths),
    )

    def update_job(**kwargs):
        with _jobs_lock:
            _active_jobs[job_id].update(kwargs)
        if "status" in kwargs:
            logger.info("Job %s status -> %s: %s", job_id, kwargs.get("status"), kwargs.get("progress", ""))

    try:
        # --- Step 1: Gemini multimodal extraction ---
        update_job(status="extracting", progress="Sending images to Gemini for analysis...")
        extractor = MultimodalExtractor()
        result = extractor.extract_and_script(all_image_paths, prompt_template=prompt_template)

        if not result:
            detail = getattr(extractor, "_last_error", None) or "could not analyze images"
            update_job(status="error", progress=f"Gemini extraction failed — {detail}")
            return

        vehicle_info = result.get("vehicle", {})
        script_info = result.get("script", {})
        photo_analysis = result.get("photo_analysis", {})

        # Build vehicle name
        year = vehicle_info.get("year", "")
        make = vehicle_info.get("make", "")
        model = vehicle_info.get("model", "")
        trim = vehicle_info.get("trim", "")
        vehicle_name = f"{year} {make} {model} {trim}".strip()

        # Save vehicle to database
        vehicle_data = {
            "cargurus_id": upload_id,
            "vin": vehicle_info.get("vin") or "",
            "year": vehicle_info.get("year") or 0,
            "make": make,
            "model": model,
            "trim": trim,
            "price": vehicle_info.get("price") or 0,
            "mileage": vehicle_info.get("mileage") or 0,
            "exterior_color": vehicle_info.get("exterior_color") or "",
            "interior_color": vehicle_info.get("interior_color") or "",
            "engine": vehicle_info.get("engine") or "",
            "transmission": vehicle_info.get("transmission") or "",
            "drivetrain": vehicle_info.get("drivetrain") or "",
            "photo_paths": json.dumps(photo_paths),
            "sticker_path": sticker_path or "",
            "video_script": json.dumps(result),
            "status": "script_generated",
            "script_generated_at": datetime.now().isoformat(),
            "prompt_template_id": int(prompt_template_id) if prompt_template_id else None,
        }
        vehicle_id = upsert_vehicle(vehicle_data)
        update_job(vehicle_id=vehicle_id, vehicle_name=vehicle_name)

        # --- Step 2: Generate AI video clip ---
        update_job(status="generating", progress=f"Generating video for {vehicle_name}...")

        veo_prompt = script_info.get("veo_prompt", "")
        if not veo_prompt:
            update_job(status="error", progress="No video prompt generated")
            return

        # Pick best reference photo
        best_idx = photo_analysis.get("best_exterior_index", 0)
        if best_idx < len(photo_paths):
            hero_photo = photo_paths[best_idx]
        else:
            hero_photo = photo_paths[0] if photo_paths else None

        sora = SoraGenerator()
        clip_path = asyncio.run(
            sora.generate_clip(veo_prompt, hero_photo, upload_id)
        )

        if not clip_path:
            sora_err = getattr(sora, "_last_error", None) or "unknown"
            update_job(status="error", progress=f"Sora video generation failed — {sora_err}")
            update_vehicle_status(vehicle_id, "error", error_message=f"Sora failed: {sora_err}")
            return

        # --- Step 3: Overlay pipeline ---
        update_job(status="compositing", progress="Adding branding and overlays...")

        overlay = VideoOverlayPipeline()
        final_path = overlay.compose_final_video(
            ai_clip_path=clip_path,
            hero_photo_path=hero_photo,
            vehicle_name=vehicle_name,
            price=vehicle_info.get("price"),
            output_name=upload_id,
            dealer_phone=overrides.get("dealer_phone") or "",
            dealer_address=overrides.get("dealer_address") or "",
            dealer_logo_path=settings.DEALER_LOGO_PATH,
            cta_text=overrides.get("cta_text") or "",
        )

        if not final_path:
            update_job(status="error", progress="Overlay compositing failed")
            update_vehicle_status(vehicle_id, "error", error_message="Overlay compositing failed")
            return

        # --- Done ---
        cost_tracker = CostTracker()
        update_vehicle_status(
            vehicle_id,
            "video_complete",
            video_path=final_path,
            video_engine="sora",
            video_cost=cost_tracker.session_cost,
            video_generated_at=datetime.now().isoformat(),
        )

        caption = script_info.get("caption", "")
        update_job(
            status="complete",
            progress="Video ready!",
            video_path=final_path,
            video_filename=Path(final_path).name,
            caption=caption,
        )

    except Exception as e:
        logger.error("Upload pipeline uncaught exception (job=%s): %s: %s", job_id, type(e).__name__, e)
        logger.debug("Upload pipeline traceback:\n%s", traceback.format_exc())
        update_job(status="error", progress=f"Pipeline error: {str(e)}")


# --- Job Status API ---

@app.route("/api/jobs")
def api_active_jobs():
    """List all active (non-terminal) jobs."""
    with _jobs_lock:
        jobs = {
            jid: dict(j, job_id=jid)
            for jid, j in _active_jobs.items()
        }
    return jsonify(jobs)


@app.route("/api/job/<job_id>")
def api_job_status(job_id):
    """Poll for job progress."""
    with _jobs_lock:
        job = _active_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


# --- Branding API ---

@app.route("/api/branding", methods=["POST"])
def api_save_branding():
    """Save dealer branding settings (logo, phone, address)."""
    logo = request.files.get("logo")
    if logo and logo.filename:
        ext = Path(logo.filename).suffix.lower() or ".png"
        logo_path = settings.BRANDING_DIR / f"dealer_logo{ext}"
        logo.save(str(logo_path))
        settings.DEALER_LOGO_PATH = str(logo_path)

    if request.form.get("phone"):
        settings.DEALER_PHONE = request.form["phone"]
    if request.form.get("address"):
        settings.DEALER_ADDRESS = request.form["address"]
    if request.form.get("dealer_name"):
        settings.DEALER_NAME = request.form["dealer_name"]
    if request.form.get("website"):
        settings.DEALER_WEBSITE = request.form["website"]

    # Persist branding to database so it survives redeployments
    save_branding_settings(
        dealer_name=settings.DEALER_NAME,
        dealer_phone=settings.DEALER_PHONE,
        dealer_address=settings.DEALER_ADDRESS,
        dealer_website=settings.DEALER_WEBSITE,
        dealer_logo_path=settings.DEALER_LOGO_PATH,
    )

    return jsonify({"status": "saved"})


@app.route("/api/branding", methods=["GET"])
def api_get_branding():
    """Get current dealer branding settings."""
    return jsonify({
        "dealer_name": settings.DEALER_NAME,
        "phone": settings.DEALER_PHONE,
        "address": settings.DEALER_ADDRESS,
        "website": settings.DEALER_WEBSITE,
        "has_logo": bool(settings.DEALER_LOGO_PATH and Path(settings.DEALER_LOGO_PATH).exists()),
    })


# --- Data APIs (kept from original) ---

@app.route("/api/stats")
def api_stats():
    stats = get_pipeline_stats()
    return jsonify(stats)


@app.route("/api/vehicles")
def api_vehicles():
    status_filter = request.args.get("status")
    if status_filter:
        vehicles = get_vehicles_by_status(status_filter)
    else:
        vehicles = get_all_vehicles()
    return jsonify(vehicles)


@app.route("/api/vehicle/<int:vehicle_id>")
def api_vehicle_detail(vehicle_id):
    vehicles = get_all_vehicles()
    vehicle = next((v for v in vehicles if v["id"] == vehicle_id), None)
    if not vehicle:
        return jsonify({"error": "Vehicle not found"}), 404

    if vehicle.get("photo_paths"):
        vehicle["photo_paths_list"] = json.loads(vehicle["photo_paths"])
    if vehicle.get("video_script"):
        vehicle["script_parsed"] = json.loads(vehicle["video_script"])

    return jsonify(vehicle)


@app.route("/api/retry-all", methods=["POST"])
def api_retry_all():
    target = "script_generated"
    if request.is_json and request.json.get("target_status"):
        target = request.json["target_status"]
    count = retry_failed_vehicles(target_status=target)
    return jsonify({"status": "ok", "reset_count": count, "target_status": target})


@app.route("/api/retry/<int:vehicle_id>", methods=["POST"])
def api_retry_vehicle(vehicle_id):
    target = "script_generated"
    if request.is_json and request.json.get("target_status"):
        target = request.json["target_status"]
    success = retry_vehicle_by_id(vehicle_id, target_status=target)
    if success:
        return jsonify({"status": "ok", "vehicle_id": vehicle_id, "target_status": target})
    return jsonify({"error": "Vehicle not found or not in error state"}), 404


# --- Prompt Templates API ---

@app.route("/api/prompt-templates")
def api_list_prompt_templates():
    """List all prompt templates."""
    return jsonify(get_all_prompt_templates())


@app.route("/api/prompt-templates", methods=["POST"])
def api_create_prompt_template():
    """Create a new prompt template."""
    data = request.get_json()
    if not data or not data.get("display_name") or not data.get("prompt_text"):
        return jsonify({"error": "display_name and prompt_text are required"}), 400
    template_id = create_prompt_template(data["display_name"], data["prompt_text"])
    return jsonify({"id": template_id, "status": "created"})


@app.route("/api/prompt-templates/<int:template_id>", methods=["PUT"])
def api_update_prompt_template(template_id):
    """Update an existing prompt template."""
    data = request.get_json()
    if not data or not data.get("display_name") or not data.get("prompt_text"):
        return jsonify({"error": "display_name and prompt_text are required"}), 400
    if update_prompt_template(template_id, data["display_name"], data["prompt_text"]):
        return jsonify({"status": "updated"})
    return jsonify({"error": "Template not found"}), 404


@app.route("/api/prompt-templates/<int:template_id>", methods=["DELETE"])
def api_delete_prompt_template(template_id):
    """Delete a prompt template."""
    if delete_prompt_template(template_id):
        return jsonify({"status": "deleted"})
    return jsonify({"error": "Template not found"}), 404


# --- File serving ---

@app.route("/videos/<path:filename>")
def serve_video(filename):
    return send_from_directory(str(settings.VIDEOS_DIR), filename)


@app.route("/photos/<path:filename>")
def serve_photo(filename):
    return send_from_directory(str(settings.PHOTOS_DIR), filename)


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(str(settings.UPLOADS_DIR), filename)


@app.route("/api/logs")
def api_recent_logs():
    """Return the most recent log entries for debugging.

    Query params:
      - lines: number of lines to return (default 100, max 500)
      - level: minimum level filter (DEBUG, INFO, WARNING, ERROR)
    """
    from utils.logger import LOG_FILE

    max_lines = min(int(request.args.get("lines", 100)), 500)
    level_filter = request.args.get("level", "").upper()

    if not LOG_FILE.exists():
        return jsonify({"lines": [], "message": "No log file yet"})

    with open(LOG_FILE, "r", encoding="utf-8") as f:
        all_lines = f.readlines()

    # Filter by level if requested
    if level_filter:
        all_lines = [l for l in all_lines if f"| {level_filter}" in l]

    recent = all_lines[-max_lines:]
    return jsonify({"lines": [l.rstrip() for l in recent], "total": len(all_lines)})


@app.route("/health")
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
