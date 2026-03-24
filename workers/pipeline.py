"""Unified video generation pipeline.

Replaces the duplicated _process_vin() and _process_upload() functions
with a single Pipeline class that handles both workflows through shared steps.
"""

import asyncio
import json
import threading
from datetime import datetime
from pathlib import Path

from config import settings
from utils.logger import get_logger
from utils.database import upsert_vehicle, update_vehicle_status
from utils.cost_tracker import CostTracker
from utils.cloud_storage import (
    upload_video as gcs_upload_video,
    is_gcs_enabled,
    upload_directory as gcs_upload_directory,
)
from video_gen.sora_generator import SoraGenerator
from video_gen.overlay import VideoOverlayPipeline

logger = get_logger("pipeline")


class Pipeline:
    """Orchestrates the vehicle video generation pipeline.

    Supports two entry modes:
      - upload: photos + sticker + carfax → Gemini extraction → Sora → overlay
      - vin: VIN decode → Gemini script → Sora → overlay

    Each step is a separate method, making it easy to add/modify/reorder stages.
    """

    def __init__(self, job_id: str, jobs_lock: threading.Lock, active_jobs: dict):
        self.job_id = job_id
        self._jobs_lock = jobs_lock
        self._active_jobs = active_jobs

    def update_job(self, **kwargs):
        with self._jobs_lock:
            self._active_jobs[self.job_id].update(kwargs)
        if "status" in kwargs:
            logger.info(
                "Job %s status -> %s: %s",
                self.job_id, kwargs.get("status"), kwargs.get("progress", ""),
            )

    # --- Step: Decode VIN ---

    def decode_vin(self, vin: str) -> dict | None:
        """Decode a VIN via NHTSA API. Returns specs dict or None."""
        from utils.vin_decoder import decode_vin as _decode_vin

        self.update_job(status="decoding", progress=f"Decoding VIN {vin}...")
        specs = _decode_vin(vin)
        if not specs:
            self.update_job(status="error", progress="Could not decode VIN — check the number and try again")
            return None
        vehicle_name = specs.get("vehicle_name", vin)
        self.update_job(vehicle_name=vehicle_name, progress=f"Decoded: {vehicle_name}")
        return specs

    # --- Step: Gemini multimodal extraction ---

    def extract_from_images(
        self,
        image_paths: list[str],
        prompt_template: dict | None = None,
        client_name: str | None = None,
        person_name: str | None = None,
    ) -> dict | None:
        """Run Gemini multimodal extraction on uploaded images."""
        from scripts.multimodal_extractor import MultimodalExtractor

        self.update_job(status="extracting", progress="Sending images to Gemini for analysis...")
        extractor = MultimodalExtractor()
        result = extractor.extract_and_script(
            image_paths,
            prompt_template=prompt_template,
            client_name=client_name,
            person_name=person_name,
        )
        if not result:
            detail = getattr(extractor, "_last_error", None) or "could not analyze images"
            self.update_job(status="error", progress=f"Gemini extraction failed — {detail}")
            return None
        return result

    # --- Step: Generate script from VIN specs ---

    def generate_vin_script(
        self,
        specs: dict,
        price=None,
        prompt_template: dict | None = None,
        client_name: str | None = None,
        person_name: str | None = None,
    ) -> dict | None:
        """Generate a video script from VIN-decoded specs."""
        from scripts.vin_script_generator import VINScriptGenerator

        vehicle_name = specs.get("vehicle_name", "vehicle")
        self.update_job(status="extracting", progress=f"Generating video script for {vehicle_name}...")
        generator = VINScriptGenerator()
        result = generator.generate(
            specs,
            price=price,
            prompt_template=prompt_template,
            client_name=client_name,
            person_name=person_name,
        )
        if not result:
            self.update_job(status="error", progress="Failed to generate video script")
            return None
        return result

    # --- Step: Save vehicle to database ---

    def save_vehicle(self, vehicle_data: dict) -> int:
        """Upsert vehicle record and update job with vehicle_id."""
        vehicle_id = upsert_vehicle(vehicle_data)
        self.update_job(vehicle_id=vehicle_id)
        return vehicle_id

    # --- Step: Generate AI video clip ---

    def generate_video(self, veo_prompt: str, reference_photo: str | None, output_name: str) -> str | None:
        """Generate a video clip via Sora. Returns clip path or None."""
        self.update_job(status="generating", progress="Generating AI video clip...")
        sora = SoraGenerator()
        clip_path = asyncio.run(
            sora.generate_clip(veo_prompt, reference_photo, output_name)
        )
        if not clip_path:
            sora_err = getattr(sora, "_last_error", None) or "unknown"
            self.update_job(status="error", progress=f"Sora video generation failed — {sora_err}")
            return None
        return clip_path

    # --- Step: Overlay compositing ---

    def apply_overlay(
        self,
        clip_path: str,
        hero_photo_path: str | None,
        vehicle_name: str,
        price=None,
        output_name: str = "",
        overrides: dict | None = None,
        vehicle_specs: dict | None = None,
    ) -> str | None:
        """Composite intro + clip + CTA outro. Returns final path or None."""
        overrides = overrides or {}
        self.update_job(status="compositing", progress="Adding branding and overlays...")
        overlay = VideoOverlayPipeline()
        final_path = overlay.compose_final_video(
            ai_clip_path=clip_path,
            hero_photo_path=hero_photo_path,
            vehicle_name=vehicle_name,
            price=price,
            output_name=output_name,
            dealer_phone=overrides.get("dealer_phone") or "",
            dealer_address=overrides.get("dealer_address") or "",
            dealer_logo_path=settings.DEALER_LOGO_PATH,
            cta_text=overrides.get("cta_text") or "",
            vehicle_specs=vehicle_specs,
        )
        if not final_path:
            self.update_job(status="error", progress="Overlay compositing failed")
            return None
        return final_path

    # --- Step: Upload to GCS ---

    def upload_to_gcs(self, final_path: str, clip_path: str | None = None) -> str | None:
        """Upload video to GCS if configured. Returns public URL or None."""
        if not is_gcs_enabled():
            return None
        self.update_job(status="uploading", progress="Uploading video to cloud storage...")
        video_url = gcs_upload_video(final_path)
        if clip_path and Path(clip_path).exists():
            gcs_upload_video(clip_path)
        if video_url:
            logger.info("Video uploaded to GCS: %s", video_url)
        else:
            logger.warning("GCS upload failed — video is still available locally")
        return video_url

    # --- Step: Record costs ---

    def record_costs(self, vehicle_id: int, final_path: str, video_url: str | None = None):
        """Record video and Gemini costs, update vehicle status to complete."""
        engine = "sora"
        quality = settings.VIDEO_QUALITY
        video_cost = settings.get_cost_per_video(engine, quality)
        gemini_cost = settings.GEMINI_COST_PER_CALL

        cost_tracker = CostTracker()
        cost_tracker.record_cost(vehicle_id, engine, quality, 20.0, video_cost, "video_generation")
        cost_tracker.record_cost(vehicle_id, "gemini", quality, 0, gemini_cost, "script_generation")

        status_kwargs = dict(
            video_path=final_path,
            video_engine=engine,
            video_cost=video_cost + gemini_cost,
            video_generated_at=datetime.now().isoformat(),
        )
        if video_url:
            status_kwargs["video_url"] = video_url
        update_vehicle_status(vehicle_id, "video_complete", **status_kwargs)

    # --- Step: Mark job complete ---

    def complete(self, final_path: str, video_url: str | None = None, caption: str = ""):
        """Mark the job as complete."""
        self.update_job(
            status="complete",
            progress="Video ready!",
            video_path=final_path,
            video_filename=Path(final_path).name,
            video_url=video_url,
            caption=caption,
        )

    def fail_vehicle(self, vehicle_id: int, error_message: str):
        """Mark vehicle as error in the database."""
        update_vehicle_status(vehicle_id, "error", error_message=error_message)


def run_upload_pipeline(
    job_id: str,
    upload_id: str,
    all_image_paths: list[str],
    photo_paths: list[str],
    sticker_path: str | None,
    overrides: dict,
    prompt_template: dict | None = None,
    prompt_template_id: str | None = None,
    person_photo_path: str | None = None,
    carfax_path: str | None = None,
    client_name: str | None = None,
    person_name: str | None = None,
    jobs_lock: threading.Lock = None,
    active_jobs: dict = None,
):
    """Background worker: extract → generate video → overlay → done."""
    import traceback

    logger.info(
        "=== Upload pipeline started — job=%s, upload=%s, images=%d ===",
        job_id, upload_id, len(all_image_paths),
    )
    pipe = Pipeline(job_id, jobs_lock, active_jobs)

    try:
        # Backup uploads to GCS
        if is_gcs_enabled():
            upload_dir = settings.UPLOADS_DIR / upload_id
            if upload_dir.is_dir():
                gcs_upload_directory(str(upload_dir), f"uploads/{upload_id}")

        # Step 1: Gemini extraction
        result = pipe.extract_from_images(
            all_image_paths,
            prompt_template=prompt_template,
            client_name=client_name,
            person_name=person_name,
        )
        if not result:
            return

        vehicle_info = result.get("vehicle", {})
        script_info = result.get("script", {})
        photo_analysis = result.get("photo_analysis", {})

        year = vehicle_info.get("year", "")
        make = vehicle_info.get("make", "")
        model = vehicle_info.get("model", "")
        trim = vehicle_info.get("trim", "")
        vehicle_name = f"{year} {make} {model} {trim}".strip()
        pipe.update_job(vehicle_name=vehicle_name)

        # Step 2: Save to database
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
            "carfax_path": carfax_path or "",
            "video_script": json.dumps(result),
            "status": "script_generated",
            "script_generated_at": datetime.now().isoformat(),
            "prompt_template_id": int(prompt_template_id) if prompt_template_id else None,
        }
        vehicle_id = pipe.save_vehicle(vehicle_data)

        # Step 3: Generate video
        veo_prompt = script_info.get("veo_prompt", "")
        if not veo_prompt:
            pipe.update_job(status="error", progress="No video prompt generated")
            return

        best_idx = photo_analysis.get("best_exterior_index", 0)
        hero_photo = photo_paths[best_idx] if best_idx < len(photo_paths) else (photo_paths[0] if photo_paths else None)
        reference_photo = person_photo_path if person_photo_path else hero_photo

        clip_path = pipe.generate_video(veo_prompt, reference_photo, upload_id)
        if not clip_path:
            pipe.fail_vehicle(vehicle_id, f"Sora failed")
            return

        # Step 4: Overlay
        final_path = pipe.apply_overlay(
            clip_path, hero_photo, vehicle_name,
            price=vehicle_info.get("price"),
            output_name=upload_id,
            overrides=overrides,
        )
        if not final_path:
            pipe.fail_vehicle(vehicle_id, "Overlay compositing failed")
            return

        # Step 5: Upload + costs
        video_url = pipe.upload_to_gcs(final_path, clip_path)
        pipe.record_costs(vehicle_id, final_path, video_url)
        pipe.complete(final_path, video_url, caption=script_info.get("caption", ""))

    except Exception as e:
        logger.error("Upload pipeline uncaught exception (job=%s): %s: %s", job_id, type(e).__name__, e)
        logger.debug("Upload pipeline traceback:\n%s", traceback.format_exc())
        pipe.update_job(status="error", progress=f"Pipeline error: {str(e)}")


def run_vin_pipeline(
    job_id: str,
    vin: str,
    overrides: dict,
    prompt_template: dict | None = None,
    prompt_template_id: str | None = None,
    person_photo_path: str | None = None,
    client_name: str | None = None,
    person_name: str | None = None,
    jobs_lock: threading.Lock = None,
    active_jobs: dict = None,
):
    """Background worker: decode VIN → generate script → generate video → overlay → done."""
    import traceback

    logger.info("=== VIN pipeline started — job=%s, vin=%s ===", job_id, vin)
    pipe = Pipeline(job_id, jobs_lock, active_jobs)

    try:
        # Step 1: Decode VIN
        specs = pipe.decode_vin(vin)
        if not specs:
            return

        vehicle_name = specs.get("vehicle_name", vin)

        # Step 2: Generate script
        price = overrides.get("price")
        result = pipe.generate_vin_script(
            specs,
            price=price,
            prompt_template=prompt_template,
            client_name=client_name,
            person_name=person_name,
        )
        if not result:
            return

        script_info = result.get("script", {})
        upload_id = f"vin_{vin}"

        # Step 3: Save to database
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
        vehicle_id = pipe.save_vehicle(vehicle_data)

        # Step 4: Generate video
        veo_prompt = script_info.get("veo_prompt", "")
        if not veo_prompt:
            pipe.update_job(status="error", progress="No video prompt generated")
            return

        clip_path = pipe.generate_video(veo_prompt, person_photo_path, upload_id)
        if not clip_path:
            pipe.fail_vehicle(vehicle_id, "Sora failed")
            return

        # Step 5: Overlay (no hero photo for VIN mode)
        final_path = pipe.apply_overlay(
            clip_path, None, vehicle_name,
            price=price,
            output_name=upload_id,
            overrides=overrides,
            vehicle_specs=specs,
        )
        if not final_path:
            pipe.fail_vehicle(vehicle_id, "Overlay compositing failed")
            return

        # Step 6: Upload + costs
        video_url = pipe.upload_to_gcs(final_path, clip_path)
        pipe.record_costs(vehicle_id, final_path, video_url)
        pipe.complete(final_path, video_url, caption=script_info.get("caption", ""))

    except Exception as e:
        logger.error("VIN pipeline uncaught exception (job=%s): %s: %s", job_id, type(e).__name__, e)
        logger.debug("VIN pipeline traceback:\n%s", traceback.format_exc())
        pipe.update_job(status="error", progress=f"Pipeline error: {str(e)}")
