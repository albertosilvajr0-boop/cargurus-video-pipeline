"""OpenAI Sora video generation.

Used as fallback/overflow when Veo budget is exhausted or Veo fails.
Uses the OpenAI responses API with video generation capabilities.
"""

import asyncio
import base64
import json
import time
from pathlib import Path

import httpx
from openai import OpenAI
from rich.console import Console

from config import settings
from utils.database import update_vehicle_status
from utils.cost_tracker import CostTracker

console = Console()


class SoraGenerator:
    """Generates videos using OpenAI Sora via the responses API."""

    def __init__(self, quality: str = None):
        self.quality = quality or settings.VIDEO_QUALITY
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.cost_tracker = CostTracker()

    async def generate_video(self, vehicle: dict, script: dict) -> str | None:
        """
        Generate a video for a vehicle using Sora.

        Args:
            vehicle: Vehicle data dict from database
            script: Parsed script dict from script generator

        Returns:
            JSON string of clip paths, or None on failure
        """
        vehicle_id = vehicle["id"]
        cg_id = vehicle["cargurus_id"]

        # Check budget
        if not self.cost_tracker.can_afford("sora", self.quality):
            console.print(f"[yellow]Budget exceeded — skipping Sora for {cg_id}[/yellow]")
            return None

        console.print(f"[cyan]  Generating Sora video for {cg_id}...[/cyan]")
        update_vehicle_status(vehicle_id, "video_generating", video_engine="sora")

        try:
            master_prompt = script.get("veo_master_prompt", "")
            extension_prompt = script.get("veo_extension_prompt", master_prompt)

            if not master_prompt:
                console.print(f"[red]  No master prompt for {cg_id}[/red]")
                return None

            # --- Generate first clip ---
            clip1_path = await self._generate_clip(
                prompt=master_prompt,
                output_name=f"{cg_id}_sora_clip1",
                reference_image=self._get_best_photo(vehicle),
            )

            if not clip1_path:
                return None

            # --- Generate second clip ---
            clip2_path = await self._generate_clip(
                prompt=extension_prompt,
                output_name=f"{cg_id}_sora_clip2",
                reference_image=self._get_best_photo(vehicle, index=1),
            )

            # Track costs
            clip_dur = settings.CLIP_DURATION["sora"]
            num_clips = 2 if clip2_path else 1
            total_seconds = clip_dur * num_clips
            cost_per_sec = settings.COST_PER_SECOND["sora"][self.quality]
            total_cost = total_seconds * cost_per_sec

            self.cost_tracker.record_cost(
                vehicle_id=vehicle_id,
                engine="sora",
                quality=self.quality,
                duration=total_seconds,
                cost=total_cost,
                call_type="video_generation",
            )

            clips = [clip1_path]
            if clip2_path:
                clips.append(clip2_path)

            return json.dumps(clips)

        except Exception as e:
            console.print(f"[red]  Sora generation failed for {cg_id}: {e}[/red]")
            update_vehicle_status(vehicle_id, "error", error_message=f"Sora: {e}")
            return None

    async def _generate_clip(self, prompt: str, output_name: str,
                              reference_image: str | None = None) -> str | None:
        """Generate a single video clip using the OpenAI responses API."""
        try:
            # Build input for the responses API
            input_parts = [{"type": "text", "text": prompt}]

            # Add reference image if available
            if reference_image and Path(reference_image).exists():
                image_data = Path(reference_image).read_bytes()
                b64_image = base64.b64encode(image_data).decode()
                input_parts.insert(0, {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}
                })

            console.print(f"[dim]    Submitting to Sora...[/dim]")

            # Use the responses API with video generation tool
            response = self.client.responses.create(
                model="sora",
                input=input_parts,
                tools=[{
                    "type": "video_generation",
                    "aspect_ratio": settings.VIDEO_ASPECT_RATIO,
                    "duration": settings.CLIP_DURATION["sora"],
                }],
            )

            # Find the video generation output
            video_url = None
            for output in response.output:
                if output.type == "video_generation_call":
                    # Poll for the video result
                    console.print(f"[dim]    Waiting for Sora generation...[/dim]")
                    max_wait = 300
                    start = time.time()

                    while time.time() - start < max_wait:
                        result = self.client.responses.retrieve(response.id)
                        for out in result.output:
                            if out.type == "video_generation_call" and hasattr(out, "video_url"):
                                video_url = out.video_url
                                break
                        if video_url:
                            break
                        await asyncio.sleep(10)

            if not video_url:
                console.print(f"[red]    ✗ No video generated by Sora[/red]")
                return None

            # Download the video
            output_path = settings.VIDEOS_DIR / f"{output_name}.mp4"
            async with httpx.AsyncClient() as http_client:
                video_response = await http_client.get(video_url)
                video_response.raise_for_status()
                output_path.write_bytes(video_response.content)

            console.print(f"[green]    ✓ Sora clip saved: {output_path.name}[/green]")
            return str(output_path)

        except Exception as e:
            console.print(f"[red]    ✗ Sora clip error: {e}[/red]")
            return None

    def _get_best_photo(self, vehicle: dict, index: int = 0) -> str | None:
        """Get the best photo path for image-to-video generation."""
        photo_paths = json.loads(vehicle.get("photo_paths", "[]"))
        if photo_paths and index < len(photo_paths):
            path = photo_paths[index]
            if Path(path).exists():
                return path
        return None
