"""Vehicle data, branding, costs, and job status API routes."""

import json
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from config import settings
from utils.database import (
    get_all_vehicles, get_vehicles_by_status, get_pipeline_stats,
    delete_vehicle, retry_failed_vehicles, retry_vehicle_by_id,
    get_cost_analytics,
    save_branding_settings, get_branding_settings,
    get_all_prompt_templates, get_prompt_template,
    create_prompt_template, update_prompt_template, delete_prompt_template,
    update_vehicle_status, get_connection,
)
from utils.cloud_storage import (
    is_gcs_enabled, upload_branding_asset,
    upload_video as gcs_upload_video,
    download_video as gcs_download_video,
)
from video_gen.overlay import VideoOverlayPipeline
from utils.logger import get_logger

import threading

logger = get_logger("routes.vehicles")

vehicles_bp = Blueprint("vehicles", __name__)

# Shared job tracking — injected from app.py via init_routes()
_jobs_lock: threading.Lock = None
_active_jobs: dict = None


def init_routes(jobs_lock: threading.Lock, active_jobs: dict):
    global _jobs_lock, _active_jobs
    _jobs_lock = jobs_lock
    _active_jobs = active_jobs


# --- Job Status ---

@vehicles_bp.route("/api/jobs")
def api_active_jobs():
    with _jobs_lock:
        jobs = {jid: dict(j, job_id=jid) for jid, j in _active_jobs.items()}
    return jsonify(jobs)


@vehicles_bp.route("/api/job/<job_id>")
def api_job_status(job_id):
    with _jobs_lock:
        job = _active_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


# --- Branding ---

@vehicles_bp.route("/api/branding", methods=["POST"])
def api_save_branding():
    logo = request.files.get("logo")
    if logo and logo.filename:
        ext = Path(logo.filename).suffix.lower() or ".png"
        logo_path = settings.BRANDING_DIR / f"dealer_logo{ext}"
        logo.save(str(logo_path))
        settings.DEALER_LOGO_PATH = str(logo_path)
        upload_branding_asset(str(logo_path))

    if request.form.get("phone"):
        settings.DEALER_PHONE = request.form["phone"]
    if request.form.get("address"):
        settings.DEALER_ADDRESS = request.form["address"]
    if request.form.get("dealer_name"):
        settings.DEALER_NAME = request.form["dealer_name"]
    if request.form.get("website"):
        settings.DEALER_WEBSITE = request.form["website"]

    save_branding_settings(
        dealer_name=settings.DEALER_NAME,
        dealer_phone=settings.DEALER_PHONE,
        dealer_address=settings.DEALER_ADDRESS,
        dealer_website=settings.DEALER_WEBSITE,
        dealer_logo_path=settings.DEALER_LOGO_PATH,
    )
    return jsonify({"status": "saved"})


@vehicles_bp.route("/api/branding", methods=["GET"])
def api_get_branding():
    has_logo = bool(settings.DEALER_LOGO_PATH and Path(settings.DEALER_LOGO_PATH).exists())
    logo_url = f"/branding/{Path(settings.DEALER_LOGO_PATH).name}" if has_logo else None
    return jsonify({
        "dealer_name": settings.DEALER_NAME,
        "phone": settings.DEALER_PHONE,
        "address": settings.DEALER_ADDRESS,
        "website": settings.DEALER_WEBSITE,
        "has_logo": has_logo,
        "logo_url": logo_url,
    })


@vehicles_bp.route("/branding/<filename>")
def serve_branding_file(filename):
    return send_from_directory(str(settings.BRANDING_DIR), filename)


# --- Costs ---

@vehicles_bp.route("/api/costs")
def api_costs():
    analytics = get_cost_analytics()
    analytics["current_rates"] = settings.COST_PER_VIDEO
    analytics["gemini_rate"] = settings.GEMINI_COST_PER_CALL
    return jsonify(analytics)


@vehicles_bp.route("/api/costs/backfill", methods=["POST"])
def api_costs_backfill():
    from utils.database import log_cost
    data = request.get_json()
    cost = float(data.get("cost_per_video", 1.20))

    conn = get_connection()
    cursor = conn.execute(
        "SELECT id, video_engine FROM vehicles "
        "WHERE video_path IS NOT NULL AND (video_cost IS NULL OR video_cost = 0)"
    )
    rows = cursor.fetchall()
    updated = 0
    for row in rows:
        vid = row["id"]
        engine = row["video_engine"] or "sora"
        conn.execute(
            "UPDATE vehicles SET video_cost = ?, updated_at = ? WHERE id = ?",
            (cost, datetime.now().isoformat(), vid)
        )
        log_cost(vid, engine, settings.VIDEO_QUALITY, 20.0, cost, "video_generation")
        updated += 1
    conn.commit()
    conn.close()
    return jsonify({"status": "ok", "backfilled": updated, "cost_per_video": cost})


# --- Vehicle Data ---

@vehicles_bp.route("/api/stats")
def api_stats():
    return jsonify(get_pipeline_stats())


@vehicles_bp.route("/api/vehicles")
def api_vehicles():
    status_filter = request.args.get("status")
    if status_filter:
        vehicles = get_vehicles_by_status(status_filter)
    else:
        vehicles = get_all_vehicles()
    return jsonify(vehicles)


@vehicles_bp.route("/api/vehicle/<int:vehicle_id>")
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


@vehicles_bp.route("/api/vehicle/<int:vehicle_id>", methods=["DELETE"])
def api_delete_vehicle(vehicle_id):
    deleted = delete_vehicle(vehicle_id)
    if not deleted:
        return jsonify({"error": "Vehicle not found"}), 404
    return jsonify({"status": "ok"})


@vehicles_bp.route("/api/retry-all", methods=["POST"])
def api_retry_all():
    target = "script_generated"
    if request.is_json and request.json.get("target_status"):
        target = request.json["target_status"]
    count = retry_failed_vehicles(target_status=target)
    return jsonify({"status": "ok", "reset_count": count, "target_status": target})


@vehicles_bp.route("/api/retry/<int:vehicle_id>", methods=["POST"])
def api_retry_vehicle(vehicle_id):
    target = "script_generated"
    if request.is_json and request.json.get("target_status"):
        target = request.json["target_status"]
    success = retry_vehicle_by_id(vehicle_id, target_status=target)
    if success:
        return jsonify({"status": "ok", "vehicle_id": vehicle_id, "target_status": target})
    return jsonify({"error": "Vehicle not found or not in error state"}), 404


# --- Prompt Templates ---

@vehicles_bp.route("/api/prompt-templates")
def api_list_prompt_templates():
    return jsonify(get_all_prompt_templates())


@vehicles_bp.route("/api/prompt-templates", methods=["POST"])
def api_create_prompt_template():
    data = request.get_json()
    if not data or not data.get("display_name") or not data.get("prompt_text"):
        return jsonify({"error": "display_name and prompt_text are required"}), 400
    template_id = create_prompt_template(data["display_name"], data["prompt_text"])
    return jsonify({"id": template_id, "status": "created"})


@vehicles_bp.route("/api/prompt-templates/<int:template_id>", methods=["PUT"])
def api_update_prompt_template(template_id):
    data = request.get_json()
    if not data or not data.get("display_name") or not data.get("prompt_text"):
        return jsonify({"error": "display_name and prompt_text are required"}), 400
    if update_prompt_template(template_id, data["display_name"], data["prompt_text"]):
        return jsonify({"status": "updated"})
    return jsonify({"error": "Template not found"}), 404


@vehicles_bp.route("/api/prompt-templates/<int:template_id>", methods=["DELETE"])
def api_delete_prompt_template(template_id):
    if delete_prompt_template(template_id):
        return jsonify({"status": "deleted"})
    return jsonify({"error": "Template not found"}), 404


# --- Video Tools (trim, frame, overlay, re-overlay) ---

@vehicles_bp.route("/api/trim", methods=["POST"])
def api_trim_video():
    data = request.get_json()
    if not data or not data.get("filename"):
        return jsonify({"error": "filename is required"}), 400

    filename = Path(data["filename"]).name
    source_path = settings.VIDEOS_DIR / filename
    if not source_path.exists():
        return jsonify({"error": "Video file not found"}), 404

    start = float(data.get("start", 0))
    end = float(data.get("end", 0))
    if end <= start:
        return jsonify({"error": "end must be greater than start"}), 400

    stem = source_path.stem
    trimmed_name = f"{stem}_trimmed_{int(start*10):04d}_{int(end*10):04d}.mp4"
    trimmed_path = settings.VIDEOS_DIR / trimmed_name
    duration = end - start

    cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-i", str(source_path),
        "-t", str(duration), "-c:v", "libx264", "-c:a", "aac",
        "-preset", "fast", "-crf", "23", str(trimmed_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error("FFmpeg trim failed: %s", result.stderr[:500])
        return jsonify({"error": "Trim failed", "detail": result.stderr[:300]}), 500

    logger.info("Video trimmed: %s -> %s (%.1fs-%.1fs)", filename, trimmed_name, start, end)
    return jsonify({
        "status": "ok",
        "trimmed_filename": trimmed_name,
        "trimmed_url": f"/videos/{trimmed_name}",
        "duration": round(duration, 2),
    })


@vehicles_bp.route("/api/vehicle/<int:vehicle_id>/frame")
def api_vehicle_frame(vehicle_id):
    conn = get_connection()
    cursor = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Vehicle not found"}), 404

    cargurus_id = row["cargurus_id"]
    time_sec = float(request.args.get("t", 1.0))

    clip_path = settings.VIDEOS_DIR / f"{cargurus_id}_clip.mp4"
    final_path = settings.VIDEOS_DIR / f"{cargurus_id}_final.mp4"
    video_path = str(clip_path) if clip_path.exists() else (str(final_path) if final_path.exists() else None)
    if not video_path:
        return jsonify({"error": "No video file found"}), 404

    overlay = VideoOverlayPipeline()
    frame_path = overlay.extract_frame(video_path, time_sec)
    if not frame_path:
        return jsonify({"error": "Frame extraction failed"}), 500

    return send_from_directory(str(Path(frame_path).parent), Path(frame_path).name, mimetype="image/jpeg")


@vehicles_bp.route("/api/overlay-image", methods=["POST"])
def api_upload_overlay_image():
    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400
    file = request.files["image"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    allowed_ext = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed_ext:
        return jsonify({"error": f"Unsupported image type: {ext}"}), 400

    safe_name = f"overlay_{uuid.uuid4().hex[:8]}{ext}"
    save_path = settings.UPLOADS_DIR / safe_name
    file.save(str(save_path))

    logger.info("Overlay image uploaded: %s -> %s", file.filename, save_path)
    return jsonify({"path": str(save_path), "url": f"/uploads/{safe_name}", "filename": file.filename})


@vehicles_bp.route("/api/video-dimensions/<int:vehicle_id>")
def api_video_dimensions(vehicle_id):
    overlay = VideoOverlayPipeline()
    return jsonify({"width": overlay.width, "height": overlay.height})


@vehicles_bp.route("/api/reoverlay", methods=["POST"])
def api_reoverlay():
    """Apply text/image overlays on existing video — $0 API cost."""
    data = request.get_json()
    if not data or not data.get("vehicle_id"):
        return jsonify({"error": "vehicle_id is required"}), 400

    vehicle_id = int(data["vehicle_id"])
    overlays = data.get("overlays", [])
    if not overlays:
        return jsonify({"error": "At least one overlay is required"}), 400

    conn = get_connection()
    cursor = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Vehicle not found"}), 404

    cargurus_id = row["cargurus_id"]
    parts = [str(row["year"] or ""), row["make"] or "", row["model"] or "", row["trim"] or ""]
    vehicle_name = " ".join(p for p in parts if p).strip() or cargurus_id

    job_id = f"reoverlay_{cargurus_id}_{uuid.uuid4().hex[:6]}"
    with _jobs_lock:
        _active_jobs[job_id] = {
            "status": "compositing",
            "progress": f"Applying text overlays for {vehicle_name} ($0 API cost)...",
            "vehicle_id": vehicle_id,
            "vehicle_name": vehicle_name,
            "started_at": datetime.now().isoformat(),
        }

    def _run_reoverlay():
        def _update_progress(percent, message):
            logger.info("Re-overlay progress [%s]: %d%% — %s", job_id, percent, message)
            with _jobs_lock:
                _active_jobs[job_id].update(progress=message, percent=percent)

        try:
            clip_local = settings.VIDEOS_DIR / f"{cargurus_id}_clip.mp4"
            final_local = settings.VIDEOS_DIR / f"{cargurus_id}_final.mp4"
            _update_progress(5, "Checking for local clip file...")

            if not clip_local.exists() and is_gcs_enabled():
                logger.info("Local clip missing, downloading from GCS: %s", clip_local.name)
                _update_progress(5, "Downloading clip from cloud storage...")
                gcs_download_video(f"videos/{clip_local.name}", str(clip_local))

            source_video = str(clip_local) if clip_local.exists() else (str(final_local) if final_local.exists() else None)
            if not source_video:
                logger.error("No video file found for %s", cargurus_id)
                with _jobs_lock:
                    _active_jobs[job_id].update(status="error", progress=f"No video file found for {cargurus_id}")
                return

            _update_progress(10, "Starting overlay pipeline...")
            overlay_pipeline = VideoOverlayPipeline()
            output_path = str(settings.VIDEOS_DIR / f"{cargurus_id}_final.mp4")

            final_path = overlay_pipeline.apply_overlays(
                video_path=source_video, overlays=overlays,
                output_path=output_path, progress_callback=_update_progress,
            )

            if not final_path:
                logger.error("Text overlay returned no output for job %s", job_id)
                with _jobs_lock:
                    _active_jobs[job_id].update(status="error", progress="Overlay failed — check server logs", percent=0)
                return

            video_url = gcs_upload_video(final_path) if is_gcs_enabled() else None

            status_kwargs = dict(video_path=final_path, video_generated_at=datetime.now().isoformat())
            if video_url:
                status_kwargs["video_url"] = video_url
            update_vehicle_status(vehicle_id, "video_complete", **status_kwargs)

            with _jobs_lock:
                _active_jobs[job_id].update(
                    status="complete", progress="Text overlays applied — $0 API cost!",
                    percent=100, video_path=final_path,
                    video_filename=Path(final_path).name, video_url=video_url,
                )

        except Exception as e:
            logger.error("Re-overlay error (job=%s): %s", job_id, e, exc_info=True)
            with _jobs_lock:
                _active_jobs[job_id].update(status="error", progress=f"Error: {e}", percent=0)

    thread = threading.Thread(target=_run_reoverlay, daemon=True)
    thread.start()

    return jsonify({
        "job_id": job_id, "vehicle_id": vehicle_id, "status": "processing",
        "message": "Applying text overlays using local FFmpeg — $0 API cost",
    })
