"""OpenAI Sora 2 video generation.

Used as fallback/overflow when Veo budget is exhausted or Veo fails.
Sora 2 generates up to 10-second clips with synchronized audio.
"""

import asyncio
import json
import time
from pathlib import Path

from openai import OpenAI
from rich.console import Console

from config import settings
from utils.database import update_vehicle_status
from utils.cost_tracker import CostTracker

console = Console()

# Sora model mapping
SORA_MODELS = {
    "fast": "sora-2",         # 720p, $0.10/sec
    "standard": "sora-2-pro", # 720p, $0.30/sec
    "pro": "sora-2-pro",      # 1080p, $0.50/sec
}


class SoraGenerator:
    """Generates videos using OpenAI Sora 2 API."""
    
    def __init__(self, quality: str = None):
        self.quality = quality or settings.VIDEO_QUALITY
        self.model = SORA_MODELS.get(self.quality, SORA_MODELS["fast"])
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.cost_tracker = CostTracker()
    
    async def generate_video(self, vehicle: dict, script: dict) -> str | None:
        """
        Generate a video for a vehicle using Sora 2.
        
        Args:
            vehicle: Vehicle data dict from database
            script: Parsed script dict from script generator
            
        Returns:
            Path to the generated video file, or None on failure
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
            # Sora 2 can do up to 10 seconds per generation
            # For 15 seconds, we need 2 clips: 10s + 5-8s
            master_prompt = script.get("veo_master_prompt", "")
            extension_prompt = script.get("veo_extension_prompt", master_prompt)
            
            if not master_prompt:
                console.print(f"[red]  No master prompt for {cg_id}[/red]")
                return None
            
            # --- Generate first clip (10 seconds) ---
            clip1_path = await self._generate_clip(
                prompt=master_prompt,
                output_name=f"{cg_id}_sora_clip1",
                duration_seconds=10,
                reference_image=self._get_best_photo(vehicle),
            )
            
            if not clip1_path:
                return None
            
            # --- Generate second clip (8 seconds) ---
            clip2_path = await self._generate_clip(
                prompt=extension_prompt,
                output_name=f"{cg_id}_sora_clip2",
                duration_seconds=8,
                reference_image=self._get_best_photo(vehicle, index=1),
            )
            
            # Track costs
            total_seconds = 10 + (8 if clip2_path else 0)
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
                              duration_seconds: int = 10,
                              reference_image: str | None = None) -> str | None:
        """Generate a single video clip using the Sora 2 API."""
        try:
            # Determine resolution and aspect ratio
            aspect_map = {
                "16:9": "landscape",
                "9:16": "portrait",
                "1:1": "square",
            }
            
            # Build Sora generation request
            # The Sora API uses the /v1/videos endpoint
            generation_params = {
                "model": self.model,
                "input": [
                    {"type": "text", "text": prompt}
                ],
                "aspect_ratio": settings.VIDEO_ASPECT_RATIO,
                "duration": duration_seconds,
            }
            
            # Add reference image for image-to-video if available
            if reference_image and Path(reference_image).exists():
                import base64
                image_data = Path(reference_image).read_bytes()
                b64_image = base64.b64encode(image_data).decode()
                generation_params["input"].insert(0, {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}
                })
            
            console.print(f"[dim]    Submitting to Sora...[/dim]")
            
            # Submit generation request
            response = self.client.videos.generate(**generation_params)
            
            # Poll for completion
            video_id = response.id
            console.print(f"[dim]    Waiting for Sora generation (ID: {video_id})...[/dim]")
            
            max_wait = 300  # 5 minutes max
            start = time.time()
            
            while time.time() - start < max_wait:
                status = self.client.videos.retrieve(video_id)
                
                if status.status == "completed":
                    # Download the video
                    output_path = settings.VIDEOS_DIR / f"{output_name}.mp4"
                    
                    video_url = status.output.url
                    import httpx
                    async with httpx.AsyncClient() as http_client:
                        video_response = await http_client.get(video_url)
                        output_path.write_bytes(video_response.content)
                    
                    console.print(f"[green]    ✓ Sora clip saved: {output_path.name}[/green]")
                    return str(output_path)
                
                elif status.status == "failed":
                    console.print(f"[red]    ✗ Sora generation failed: {status.error}[/red]")
                    return None
                
                await asyncio.sleep(10)
            
            console.print(f"[red]    ✗ Sora generation timed out[/red]")
            return None
            
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
