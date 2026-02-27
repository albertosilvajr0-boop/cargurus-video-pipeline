"""Downloads vehicle photos and window stickers from scraped URLs."""

import asyncio
import json
from pathlib import Path

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from config import settings
from utils.database import get_vehicles_by_status, update_vehicle_status

console = Console()


class AssetDownloader:
    """Downloads vehicle photos and window stickers."""
    
    def __init__(self):
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
    
    async def download_all(self):
        """Download assets for all scraped vehicles."""
        vehicles = get_vehicles_by_status("scraped")
        
        if not vehicles:
            console.print("[yellow]No vehicles pending asset download[/yellow]")
            return
        
        console.print(f"[cyan]Downloading assets for {len(vehicles)} vehicles...[/cyan]")
        
        async with httpx.AsyncClient(
            headers=self.headers,
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                console=console,
            ) as progress:
                task = progress.add_task("Downloading...", total=len(vehicles))
                
                for vehicle in vehicles:
                    try:
                        await self._download_vehicle_assets(client, vehicle)
                    except Exception as e:
                        console.print(f"[red]Error downloading for vehicle {vehicle['id']}: {e}[/red]")
                        update_vehicle_status(vehicle["id"], "error", error_message=str(e))
                    
                    progress.update(task, advance=1)
                    await asyncio.sleep(0.5)  # Rate limiting
    
    async def _download_vehicle_assets(self, client: httpx.AsyncClient, vehicle: dict):
        """Download photos and window sticker for a single vehicle."""
        vehicle_id = vehicle["id"]
        cg_id = vehicle["cargurus_id"]
        
        # Create vehicle-specific directories
        photo_dir = settings.PHOTOS_DIR / cg_id
        photo_dir.mkdir(parents=True, exist_ok=True)
        
        # --- Download photos ---
        photo_urls = json.loads(vehicle.get("photo_urls", "[]"))
        downloaded_photos = []
        
        for i, url in enumerate(photo_urls[:10]):  # Max 10 photos per vehicle
            try:
                photo_path = photo_dir / f"photo_{i:02d}.jpg"
                if not photo_path.exists():
                    response = await client.get(url)
                    response.raise_for_status()
                    photo_path.write_bytes(response.content)
                downloaded_photos.append(str(photo_path))
            except Exception as e:
                console.print(f"[dim red]Failed to download photo {i} for {cg_id}: {e}[/dim red]")
        
        # --- Download window sticker ---
        sticker_path = None
        sticker_url = vehicle.get("sticker_url")
        
        if sticker_url:
            try:
                sticker_file = settings.STICKERS_DIR / f"{cg_id}_sticker.pdf"
                if sticker_url.lower().endswith((".jpg", ".jpeg", ".png")):
                    sticker_file = sticker_file.with_suffix(Path(sticker_url).suffix)
                
                if not sticker_file.exists():
                    response = await client.get(sticker_url)
                    response.raise_for_status()
                    sticker_file.write_bytes(response.content)
                
                sticker_path = str(sticker_file)
            except Exception as e:
                console.print(f"[dim yellow]No window sticker for {cg_id}: {e}[/dim yellow]")
        
        # Update database
        new_status = "sticker_downloaded" if sticker_path else "photos_downloaded"
        update_vehicle_status(
            vehicle_id,
            new_status,
            photo_paths=json.dumps(downloaded_photos),
            sticker_path=sticker_path,
        )
        
        console.print(
            f"[dim]  {cg_id}: {len(downloaded_photos)} photos"
            f"{', sticker ✓' if sticker_path else ''}[/dim]"
        )


async def run_downloader():
    """Convenience function to run the downloader."""
    downloader = AssetDownloader()
    await downloader.download_all()
