"""OpenAI Sora video generation.

Simplified for the upload-first workflow: generates a single video clip.
"""

import asyncio
import io
import time
import traceback
from pathlib import Path

import httpx
from openai import OpenAI
from PIL import Image
from rich.console import Console

from config import settings
from utils.cost_tracker import CostTracker
from utils.logger import get_logger
from utils.retry import retry_async

console = Console()
logger = get_logger("sora")

# Map aspect ratio setting to Sora size format (width x height)
SORA_SIZES = {
    "9:16": "720x1280",
    "16:9": "1280x720",
}


class SoraGenerator:
    """Generates a single video clip using OpenAI Sora."""

    def __init__(self, quality: str = None):
        self.quality = quality or settings.VIDEO_QUALITY
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.cost_tracker = CostTracker()
        self._last_error: str | None = None
        logger.info("SoraGenerator initialized (quality=%s)", self.quality)

    async def generate_clip(
        self, prompt: str, reference_image_path: str | None, output_name: str
    ) -> str | None:
        """
        Generate a single video clip.

        Args:
            prompt: The cinematic video generation prompt
            reference_image_path: Path to hero photo for image-to-video
            output_name: Base name for output file

        Returns:
            Path to generated clip, or None on failure
        """
        if not self.cost_tracker.can_afford("sora", self.quality):
            self._last_error = f"Budget exceeded (remaining: ${self.cost_tracker.remaining_budget:.2f})"
            logger.warning("Sora budget check failed: %s", self._last_error)
            console.print(f"[yellow]Sora: {self._last_error}[/yellow]")
            return None

        logger.info(
            "Sora generate_clip starting — output=%s, has_reference=%s, prompt_length=%d",
            output_name,
            bool(reference_image_path and Path(reference_image_path).exists()),
            len(prompt),
        )
        logger.debug("Sora prompt: %s", prompt[:500])
        console.print("[cyan]Generating Sora clip...[/cyan]")

        try:
            clip_path = await self._generate(prompt, reference_image_path, output_name)

            if clip_path:
                clip_dur = settings.CLIP_DURATION["sora"]
                cost = clip_dur * settings.COST_PER_SECOND["sora"][self.quality]
                self.cost_tracker.record_cost(
                    vehicle_id=0,
                    engine="sora",
                    quality=self.quality,
                    duration=clip_dur,
                    cost=cost,
                    call_type="video_generation",
                )
                logger.info("Sora clip saved: %s (cost=$%.4f)", Path(clip_path).name, cost)
                console.print(f"[green]Sora clip saved: {Path(clip_path).name}[/green]")
            else:
                self._last_error = self._last_error or "Sora returned no video"
                logger.warning("Sora returned no clip: %s", self._last_error)

            return clip_path

        except Exception as e:
            self._last_error = f"Sora API error: {type(e).__name__}: {e}"
            logger.error("Sora generate_clip failed: %s", self._last_error)
            logger.debug("Sora traceback:\n%s", traceback.format_exc())
            console.print(f"[red]{self._last_error}[/red]")
            return None

    @retry_async(max_retries=3, base_delay=5.0, max_delay=60.0, operation_name="Sora clip generation")
    async def _generate(
        self, prompt: str, reference_image_path: str | None, output_name: str
    ) -> str | None:
        """Generate clip with retry logic."""
        size = SORA_SIZES.get(settings.VIDEO_ASPECT_RATIO, "720x1280")
        # Sora 2 API accepts seconds values of 5, 10, 15, or 20
        raw_duration = settings.CLIP_DURATION.get("sora", 20)
        valid_durations = [5, 10, 15, 20]
        duration = min(d for d in valid_durations if d >= raw_duration) if raw_duration <= 20 else 20

        # Build request payload
        payload = {
            "model": "sora-2",
            "prompt": prompt,
            "size": size,
            "seconds": duration,
        }

        logger.info(
            "Sora API request — model=%s, size=%s, duration=%ds, has_image=%s",
            payload["model"], size, duration,
            bool(reference_image_path and Path(reference_image_path).exists()),
        )

        # Add reference image if provided
        has_reference = reference_image_path and Path(reference_image_path).exists()
        if has_reference:
            ref_path = Path(reference_image_path)
            target_w, target_h = (int(d) for d in size.split("x"))
            img = Image.open(ref_path).convert("RGB")
            original_size = img.size
            img = img.resize((target_w, target_h), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            image_bytes = buf.getvalue()
            logger.debug(
                "Reference image prepared: %s (%dx%d -> %dx%d, %d bytes JPEG)",
                ref_path.name, original_size[0], original_size[1],
                target_w, target_h, len(image_bytes),
            )

        if has_reference:
            # Use multipart/form-data with the image file (matches Sora API spec)
            logger.debug("Sending multipart POST to https://api.openai.com/v1/videos")
            try:
                resp = httpx.post(
                    "https://api.openai.com/v1/videos",
                    headers={
                        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    },
                    data={
                        "model": "sora-2",
                        "prompt": prompt,
                        "size": size,
                        "seconds": str(duration),
                    },
                    files={
                        "input_reference": ("reference.jpg", image_bytes, "image/jpeg"),
                    },
                    timeout=60,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                response_body = e.response.text
                logger.error(
                    "Sora HTTP %d error — URL: %s | Response body: %s",
                    e.response.status_code, str(e.request.url), response_body,
                )
                if e.response.status_code == 400:
                    # 400 with image reference — retry without the image as text-to-video
                    logger.warning(
                        "Sora 400 with image reference — retrying as text-to-video (no image)"
                    )
                    console.print("[yellow]Sora image-to-video failed (400), retrying text-only...[/yellow]")
                    has_reference = False  # fall through to SDK path below
                else:
                    self._last_error = (
                        f"Sora API HTTP {e.response.status_code}: {response_body[:300]}"
                    )
                    console.print(f"[red]Sora HTTP {e.response.status_code}: {response_body[:300]}[/red]")
                    raise

        if has_reference:
            job_data = resp.json()
            job_id = job_data["id"]
            logger.info("Sora job created via HTTP: %s", job_id)
        else:
            try:
                video_job = self.client.videos.create(**payload)
                job_id = video_job.id
                logger.info("Sora job created via SDK: %s", job_id)
            except Exception as e:
                # Log the full exception details for SDK errors too
                logger.error(
                    "Sora SDK create failed: %s: %s",
                    type(e).__name__, e,
                )
                if hasattr(e, "response") and e.response is not None:
                    logger.error("Sora SDK response body: %s", e.response.text[:500])
                raise

        console.print(f"[dim]Sora job created: {job_id} — polling for completion...[/dim]")

        max_wait = 900  # 15-minute safety limit
        start = time.time()
        poll_count = 0
        last_status = None

        while True:
            poll_count += 1
            elapsed = time.time() - start

            try:
                status = self.client.videos.retrieve(job_id)
            except Exception as e:
                logger.error(
                    "Sora poll #%d failed — job=%s elapsed=%.0fs error=%s: %s",
                    poll_count, job_id, elapsed, type(e).__name__, e,
                )
                self._last_error = f"Sora polling error: {type(e).__name__}: {e}"
                console.print(f"[red]{self._last_error}[/red]")
                return None

            if status.status != last_status:
                logger.info(
                    "Sora job %s status changed: %s -> %s (poll #%d, %.0fs elapsed)",
                    job_id, last_status, status.status, poll_count, elapsed,
                )
                last_status = status.status
            else:
                logger.debug(
                    "Sora poll #%d (%.0fs elapsed) — job=%s status=%s",
                    poll_count, elapsed, job_id, status.status,
                )

            if status.status == "completed":
                output_path = settings.VIDEOS_DIR / f"{output_name}_clip.mp4"
                download_url = f"https://api.openai.com/v1/videos/{job_id}/content"
                dl_resp = httpx.get(
                    download_url,
                    headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                    timeout=120,
                    follow_redirects=True,
                )
                dl_resp.raise_for_status()
                output_path.write_bytes(dl_resp.content)
                logger.info(
                    "Sora job %s completed in %.0fs (%d polls) — saved to %s (%d bytes)",
                    job_id, elapsed, poll_count, output_path.name, len(dl_resp.content),
                )
                return str(output_path)

            if status.status == "failed":
                error_detail = getattr(status, "error", "unknown")
                self._last_error = f"Sora job failed: {error_detail}"
                logger.error(
                    "Sora job %s failed after %.0fs (%d polls) — error: %s | full status: %s",
                    job_id, elapsed, poll_count, error_detail, status,
                )
                console.print(f"[red]{self._last_error}[/red]")
                return None

            if elapsed >= max_wait:
                self._last_error = (
                    f"Sora job {job_id} still '{status.status}' after {elapsed:.0f}s "
                    f"({poll_count} polls) — giving up"
                )
                logger.error(
                    "Sora timeout — job=%s status=%s elapsed=%.0fs polls=%d last_status_obj=%s",
                    job_id, status.status, elapsed, poll_count, status,
                )
                console.print(f"[red]{self._last_error}[/red]")
                return None

            # Progressive polling: 5s for first 2 min, 10s for 2-5 min, 15s after
            if elapsed < 120:
                poll_interval = 5
            elif elapsed < 300:
                poll_interval = 10
            else:
                poll_interval = 15
            await asyncio.sleep(poll_interval)
