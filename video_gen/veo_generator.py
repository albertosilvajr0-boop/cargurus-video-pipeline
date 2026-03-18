"""Google Veo video generation via the google-genai SDK.

Simplified for the upload-first workflow: generates a single 8-second
cinematic clip from a prompt + reference photo. The overlay pipeline
handles intro/outro/branding separately.
"""

import asyncio
import time
import traceback
from pathlib import Path

import httpx
from google import genai
from google.genai import types
from rich.console import Console

from config import settings
from utils.cost_tracker import CostTracker
from utils.logger import get_logger
from utils.retry import retry_async

console = Console()
logger = get_logger("veo")

VEO_MODELS = {
    "fast": "veo-3.1-fast-generate-preview",
    "standard": "veo-3.1-generate-preview",
    "pro": "veo-3.0-generate-001",
}


class VeoGenerator:
    """Generates a single video clip using Google Veo."""

    def __init__(self, quality: str = None):
        self.quality = quality or settings.VIDEO_QUALITY
        self.model_name = VEO_MODELS.get(self.quality, VEO_MODELS["fast"])
        self.cost_tracker = CostTracker()
        self.client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        self._last_error: str | None = None
        logger.info("VeoGenerator initialized (quality=%s, model=%s)", self.quality, self.model_name)

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
        if not self.cost_tracker.can_afford("veo", self.quality):
            self._last_error = f"Budget exceeded (remaining: ${self.cost_tracker.remaining_budget:.2f})"
            logger.warning("Veo budget check failed: %s", self._last_error)
            console.print(f"[yellow]Veo: {self._last_error}[/yellow]")
            return None

        logger.info(
            "Veo generate_clip starting — model=%s, output=%s, has_reference=%s, prompt_length=%d",
            self.model_name, output_name,
            bool(reference_image_path and Path(reference_image_path).exists()),
            len(prompt),
        )
        logger.debug("Veo prompt: %s", prompt[:500])
        console.print(f"[cyan]Generating Veo clip ({self.model_name})...[/cyan]")

        try:
            clip_path = await self._generate(prompt, reference_image_path, output_name)

            if clip_path:
                clip_dur = settings.CLIP_DURATION["veo"]
                cost = clip_dur * settings.COST_PER_SECOND["veo"][self.quality]
                self.cost_tracker.record_cost(
                    vehicle_id=0,
                    engine="veo",
                    quality=self.quality,
                    duration=clip_dur,
                    cost=cost,
                    call_type="video_generation",
                )
                logger.info("Veo clip saved: %s (cost=$%.4f)", Path(clip_path).name, cost)
                console.print(f"[green]Veo clip saved: {Path(clip_path).name}[/green]")
            else:
                self._last_error = self._last_error or "Veo returned no video"
                logger.warning("Veo returned no clip: %s", self._last_error)

            return clip_path

        except Exception as e:
            self._last_error = f"Veo API error: {type(e).__name__}: {e}"
            logger.error("Veo generate_clip failed: %s", self._last_error)
            logger.debug("Veo traceback:\n%s", traceback.format_exc())
            console.print(f"[red]{self._last_error}[/red]")
            return None

    @retry_async(max_retries=3, base_delay=5.0, max_delay=60.0, operation_name="Veo clip generation")
    async def _generate(
        self, prompt: str, reference_image_path: str | None, output_name: str
    ) -> str | None:
        """Generate clip with retry logic."""
        image = None
        if reference_image_path and Path(reference_image_path).exists():
            image_bytes = Path(reference_image_path).read_bytes()
            image = types.Image(image_bytes=image_bytes, mime_type="image/jpeg")
            logger.debug(
                "Veo reference image loaded: %s (%d bytes)",
                Path(reference_image_path).name, len(image_bytes),
            )

        logger.info(
            "Veo API request — model=%s, aspect=%s, has_image=%s",
            self.model_name, settings.VIDEO_ASPECT_RATIO, image is not None,
        )

        try:
            operation = self.client.models.generate_videos(
                model=self.model_name,
                prompt=prompt,
                image=image,
                config=types.GenerateVideosConfig(
                    aspect_ratio=settings.VIDEO_ASPECT_RATIO,
                ),
            )
        except Exception as e:
            logger.error(
                "Veo generate_videos call failed: %s: %s",
                type(e).__name__, e,
            )
            if hasattr(e, "message"):
                logger.error("Veo API error message: %s", e.message)
            raise

        logger.info("Veo operation started — polling for completion...")
        console.print("[dim]Waiting for Veo generation...[/dim]")
        max_wait = 300
        start = time.time()
        poll_count = 0

        while not operation.done:
            elapsed = time.time() - start
            if elapsed > max_wait:
                self._last_error = "Veo generation timed out (5 min)"
                logger.error("Veo timed out after %d polls (%.0fs)", poll_count, elapsed)
                console.print(f"[red]{self._last_error}[/red]")
                return None
            await asyncio.sleep(10)
            poll_count += 1
            operation = self.client.operations.get(operation)
            logger.debug("Veo poll #%d (%.0fs elapsed) — done=%s", poll_count, elapsed, operation.done)

        elapsed = time.time() - start

        if operation.result and operation.result.generated_videos:
            video = operation.result.generated_videos[0]
            output_path = settings.VIDEOS_DIR / f"{output_name}_clip.mp4"

            # Try local bytes first, fall back to downloading from URI
            if video.video.video_bytes:
                output_path.write_bytes(video.video.video_bytes)
                logger.info("Veo video saved from bytes — %s (%.0fs)", output_path.name, elapsed)
            elif video.video.uri:
                # The URI requires authentication — append the API key
                uri = video.video.uri
                separator = "&" if "?" in uri else "?"
                authed_uri = f"{uri}{separator}key={settings.GOOGLE_API_KEY}"
                logger.debug("Veo downloading video from URI: %s...", uri[:80])
                async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                    resp = await client.get(authed_uri)
                    resp.raise_for_status()
                    output_path.write_bytes(resp.content)
                logger.info(
                    "Veo video downloaded from URI — %s (%d bytes, %.0fs)",
                    output_path.name, len(resp.content), elapsed,
                )
            else:
                self._last_error = "Veo returned video with no bytes or URI"
                logger.error("Veo completed but video has no bytes or URI — operation result: %s", operation.result)
                return None

            return str(output_path)

        self._last_error = "Veo operation completed but returned no video"
        logger.error(
            "Veo operation completed with no video after %.0fs — result: %s",
            elapsed, operation.result,
        )
        console.print(f"[red]{self._last_error}[/red]")
        return None
