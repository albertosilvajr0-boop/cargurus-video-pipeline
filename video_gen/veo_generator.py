"""Google Veo video generation via the google-genai SDK.

Uses Veo 3.1 Fast (primary) or Veo 3.1 Standard for generating
cinematic vehicle videos from AI-generated scripts.
"""

import asyncio
import json
import time
from pathlib import Path

from google import genai
from google.genai import types
from rich.console import Console

from config import settings
from utils.database import update_vehicle_status
from utils.cost_tracker import CostTracker

console = Console()

# Veo model mapping
VEO_MODELS = {
    "fast": "veo-3.1-fast-generate-preview",
    "standard": "veo-3.1-generate-preview",
    "pro": "veo-3.0-generate-001",
}


class VeoGenerator:
    """Generates videos using Google Veo via the google-genai SDK."""

    def __init__(self, quality: str = None):
        self.quality = quality or settings.VIDEO_QUALITY
        self.model_name = VEO_MODELS.get(self.quality, VEO_MODELS["fast"])
        self.cost_tracker = CostTracker()
        self.client = genai.Client(api_key=settings.GOOGLE_API_KEY)

    async def generate_video(self, vehicle: dict, script: dict) -> str | None:
        """
        Generate a video for a vehicle using Veo.

        Args:
            vehicle: Vehicle data dict from database
            script: Parsed script dict from script generator

        Returns:
            JSON string of clip paths, or None on failure
        """
        vehicle_id = vehicle["id"]
        cg_id = vehicle["cargurus_id"]

        # Check budget
        if not self.cost_tracker.can_afford("veo", self.quality):
            console.print(f"[yellow]Budget exceeded — skipping Veo for {cg_id}[/yellow]")
            return None

        console.print(f"[cyan]  Generating Veo video for {cg_id}...[/cyan]")
        update_vehicle_status(vehicle_id, "video_generating", video_engine="veo")

        try:
            # --- Generate first clip (8 seconds) ---
            master_prompt = script.get("veo_master_prompt", "")
            if not master_prompt:
                console.print(f"[red]  No master prompt for {cg_id}[/red]")
                return None

            clip1_path = await self._generate_clip(
                prompt=master_prompt,
                output_name=f"{cg_id}_clip1",
                reference_image=self._get_best_photo(vehicle),
            )

            if not clip1_path:
                return None

            # --- Generate second clip (8 seconds) for 15-second total ---
            extension_prompt = script.get("veo_extension_prompt", master_prompt)
            clip2_path = await self._generate_clip(
                prompt=extension_prompt,
                output_name=f"{cg_id}_clip2",
                reference_image=self._get_best_photo(vehicle, index=1),
            )

            # --- Track costs ---
            clip_duration = settings.CLIP_DURATION["veo"]
            cost_per_sec = settings.COST_PER_SECOND["veo"][self.quality]
            num_clips = 2 if clip2_path else 1
            total_cost = clip_duration * cost_per_sec * num_clips

            self.cost_tracker.record_cost(
                vehicle_id=vehicle_id,
                engine="veo",
                quality=self.quality,
                duration=clip_duration * num_clips,
                cost=total_cost,
                call_type="video_generation",
            )

            # --- Return clip paths for stitching ---
            clips = [clip1_path]
            if clip2_path:
                clips.append(clip2_path)

            return json.dumps(clips)

        except Exception as e:
            console.print(f"[red]  Veo generation failed for {cg_id}: {e}[/red]")
            update_vehicle_status(vehicle_id, "error", error_message=f"Veo: {e}")
            return None

    async def _generate_clip(self, prompt: str, output_name: str,
                              reference_image: str | None = None) -> str | None:
        """Generate a single video clip using the Veo API."""
        try:
            # Build image reference if available
            image = None
            if reference_image and Path(reference_image).exists():
                image_bytes = Path(reference_image).read_bytes()
                image = types.Image(
                    image_bytes=image_bytes,
                    mime_type="image/jpeg",
                )

            # Generate video via google-genai SDK
            console.print(f"[dim]    Submitting to Veo ({self.model_name})...[/dim]")

            operation = self.client.models.generate_videos(
                model=self.model_name,
                prompt=prompt,
                image=image,
                config=types.GenerateVideosConfig(
                    aspect_ratio=settings.VIDEO_ASPECT_RATIO,
                    person_generation="allow_all",
                ),
            )

            # Poll for completion with timeout
            console.print(f"[dim]    Waiting for Veo generation...[/dim]")
            max_wait = 300  # 5 minutes
            start = time.time()

            while not operation.done:
                if time.time() - start > max_wait:
                    console.print(f"[red]    ✗ Veo generation timed out[/red]")
                    return None
                await asyncio.sleep(10)
                operation = self.client.operations.get(operation)

            if operation.result and operation.result.generated_videos:
                video = operation.result.generated_videos[0]

                # Save the generated video
                output_path = settings.VIDEOS_DIR / f"{output_name}.mp4"
                video.video.save(str(output_path))

                console.print(f"[green]    ✓ Clip saved: {output_path.name}[/green]")
                return str(output_path)
            else:
                console.print(f"[red]    ✗ No video generated[/red]")
                return None

        except Exception as e:
            console.print(f"[red]    ✗ Clip generation error: {e}[/red]")
            return None

    def _get_best_photo(self, vehicle: dict, index: int = 0) -> str | None:
        """Get the best photo path for image-to-video generation."""
        photo_paths = json.loads(vehicle.get("photo_paths", "[]"))
        if photo_paths and index < len(photo_paths):
            path = photo_paths[index]
            if Path(path).exists():
                return path
        return None
