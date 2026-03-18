"""OpenAI Sora video generation.

Simplified for the upload-first workflow: generates a single video clip
as fallback when Veo fails or budget prefers Sora.
"""

import asyncio
import base64
import io
import time
from pathlib import Path

import httpx
from openai import OpenAI
from PIL import Image
from rich.console import Console

from config import settings
from utils.cost_tracker import CostTracker
from utils.retry import retry_async

console = Console()

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
            console.print(f"[yellow]Sora: {self._last_error}[/yellow]")
            return None

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
                console.print(f"[green]Sora clip saved: {Path(clip_path).name}[/green]")
            else:
                self._last_error = self._last_error or "Sora returned no video"

            return clip_path

        except Exception as e:
            self._last_error = f"Sora API error: {type(e).__name__}: {e}"
            console.print(f"[red]{self._last_error}[/red]")
            return None

    @retry_async(max_retries=3, base_delay=5.0, max_delay=60.0, operation_name="Sora clip generation")
    async def _generate(
        self, prompt: str, reference_image_path: str | None, output_name: str
    ) -> str | None:
        """Generate clip with retry logic."""
        size = SORA_SIZES.get(settings.VIDEO_ASPECT_RATIO, "720x1280")
        duration = min(settings.CLIP_DURATION.get("sora", 8), 12)

        # Build request payload
        payload = {
            "model": "sora-2",
            "prompt": prompt,
            "size": size,
            "seconds": duration,
        }

        # Add reference image if provided
        has_reference = reference_image_path and Path(reference_image_path).exists()
        if has_reference:
            ref_path = Path(reference_image_path)
            target_w, target_h = (int(d) for d in size.split("x"))
            img = Image.open(ref_path).convert("RGB")
            img = img.resize((target_w, target_h), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            b64 = base64.b64encode(buf.getvalue()).decode()
            payload["input_reference"] = {
                "image_url": f"data:image/jpeg;base64,{b64}"
            }

        if has_reference:
            # The Python SDK sends multipart/form-data which the API rejects
            # for dict-typed input_reference. Use a direct JSON request instead.
            resp = httpx.post(
                "https://api.openai.com/v1/videos",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            job_data = resp.json()
            job_id = job_data["id"]
        else:
            video_job = self.client.videos.create(**payload)
            job_id = video_job.id

        console.print(f"[dim]Sora job created: {job_id} — polling for completion...[/dim]")

        max_wait = 300
        start = time.time()

        while time.time() - start < max_wait:
            status = self.client.videos.retrieve(job_id)

            if status.status == "completed":
                # Download the video content
                output_path = settings.VIDEOS_DIR / f"{output_name}_clip.mp4"
                video_content = self.client.videos.content(job_id)
                output_path.write_bytes(video_content.read())
                return str(output_path)

            if status.status == "failed":
                self._last_error = f"Sora job failed: {getattr(status, 'error', 'unknown')}"
                console.print(f"[red]{self._last_error}[/red]")
                return None

            await asyncio.sleep(10)

        self._last_error = "Sora timed out after 5 min"
        console.print(f"[red]{self._last_error}[/red]")
        return None
