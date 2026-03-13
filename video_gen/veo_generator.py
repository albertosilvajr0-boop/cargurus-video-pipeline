"""Google Veo video generation via the google-genai SDK.

Simplified for the upload-first workflow: generates a single 8-second
cinematic clip from a prompt + reference photo. The overlay pipeline
handles intro/outro/branding separately.
"""

import asyncio
import time
from pathlib import Path

from google import genai
from google.genai import types
from rich.console import Console

from config import settings
from utils.cost_tracker import CostTracker
from utils.retry import retry_async

console = Console()

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
            console.print(f"[yellow]Veo: {self._last_error}[/yellow]")
            return None

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
                console.print(f"[green]Veo clip saved: {Path(clip_path).name}[/green]")
            else:
                self._last_error = self._last_error or "Veo returned no video"

            return clip_path

        except Exception as e:
            self._last_error = f"Veo API error: {type(e).__name__}: {e}"
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

        operation = self.client.models.generate_videos(
            model=self.model_name,
            prompt=prompt,
            image=image,
            config=types.GenerateVideosConfig(
                aspect_ratio=settings.VIDEO_ASPECT_RATIO,
                person_generation="allow_all",
            ),
        )

        console.print("[dim]Waiting for Veo generation...[/dim]")
        max_wait = 300
        start = time.time()

        while not operation.done:
            if time.time() - start > max_wait:
                self._last_error = "Veo generation timed out (5 min)"
                console.print(f"[red]{self._last_error}[/red]")
                return None
            await asyncio.sleep(10)
            operation = self.client.operations.get(operation)

        if operation.result and operation.result.generated_videos:
            video = operation.result.generated_videos[0]
            output_path = settings.VIDEOS_DIR / f"{output_name}_clip.mp4"
            video.video.save(str(output_path))
            return str(output_path)

        self._last_error = "Veo operation completed but returned no video"
        console.print(f"[red]{self._last_error}[/red]")
        return None
