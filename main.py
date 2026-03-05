#!/usr/bin/env python3
"""
CarGurus Vehicle Video Pipeline
================================
Automated pipeline that scrapes San Antonio Dodge's CarGurus inventory,
generates AI video scripts, and produces cinematic videos using Veo and Sora.

Usage:
    python main.py                    # Run full pipeline
    python main.py --step scrape      # Scrape inventory only
    python main.py --step download    # Download photos/stickers
    python main.py --step scripts     # Generate video scripts
    python main.py --step videos      # Generate videos
    python main.py --step status      # Show pipeline status
    python main.py --max 5            # Limit to 5 vehicles
    python main.py --quality standard # Set video quality
"""

import asyncio
import json
from datetime import datetime

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from config import settings
from utils.database import (
    init_db, get_all_vehicles, get_vehicles_by_status,
    get_pipeline_stats, update_vehicle_status, retry_failed_vehicles,
)
from utils.cost_tracker import CostTracker
from scraper.cargurus_scraper import run_scraper
from scraper.asset_downloader import run_downloader
from scripts.script_generator import run_script_generator
from video_gen.veo_generator import VeoGenerator
from video_gen.sora_generator import SoraGenerator
from video_gen.video_stitcher import VideoStitcher

console = Console()


def print_banner():
    """Print the pipeline banner."""
    console.print(Panel.fit(
        "[bold cyan]🎬 CarGurus Vehicle Video Pipeline[/bold cyan]\n"
        f"[dim]Dealer: {settings.DEALER_NAME}[/dim]\n"
        f"[dim]Engine: {settings.PRIMARY_VIDEO_ENGINE.upper()} ({settings.VIDEO_QUALITY}) "
        f"| Budget: ${settings.COST_LIMIT:.2f}[/dim]",
        border_style="cyan",
    ))


def print_status():
    """Print current pipeline status."""
    stats = get_pipeline_stats()
    
    table = Table(title="📊 Pipeline Status")
    table.add_column("Status", style="cyan")
    table.add_column("Count", style="white", justify="right")
    
    status_order = [
        ("scraped", "🔍 Scraped"),
        ("photos_downloaded", "📸 Photos Downloaded"),
        ("sticker_downloaded", "🏷️ Sticker Downloaded"),
        ("script_generated", "📝 Script Generated"),
        ("video_generating", "🎬 Video Generating"),
        ("video_complete", "✅ Video Complete"),
        ("error", "❌ Error"),
    ]
    
    for status_key, label in status_order:
        count = stats["by_status"].get(status_key, 0)
        if count > 0:
            table.add_row(label, str(count))
    
    table.add_section()
    table.add_row("[bold]Total Vehicles[/bold]", f"[bold]{stats['total_vehicles']}[/bold]")
    table.add_row("[bold]Videos Completed[/bold]", f"[bold]{stats['videos_completed']}[/bold]")
    table.add_row("[bold]Total Cost[/bold]", f"[bold green]${stats['total_cost']:.2f}[/bold green]")
    
    console.print(table)


async def run_video_generation():
    """Generate videos for all vehicles with scripts."""
    vehicles = get_vehicles_by_status("script_generated")
    
    if not vehicles:
        console.print("[yellow]No vehicles pending video generation[/yellow]")
        return
    
    console.print(f"[cyan]Generating videos for {len(vehicles)} vehicles...[/cyan]")
    
    cost_tracker = CostTracker()
    veo = VeoGenerator()
    sora = SoraGenerator()
    stitcher = VideoStitcher()
    
    for vehicle in vehicles:
        # Check overall budget
        engine, quality = cost_tracker.get_best_engine()
        if engine is None:
            console.print("[red]⚠ Budget exhausted! Stopping video generation.[/red]")
            break
        
        script = json.loads(vehicle.get("video_script", "{}"))
        if not script:
            continue
        
        cg_id = vehicle["cargurus_id"]
        year = vehicle.get("year", "")
        make = vehicle.get("make", "")
        model = vehicle.get("model", "")
        
        console.print(f"\n[bold]Processing: {year} {make} {model} ({cg_id})[/bold]")
        
        # Try primary engine first
        clip_paths_json = None
        
        if engine == "veo" or settings.PRIMARY_VIDEO_ENGINE == "veo":
            clip_paths_json = await veo.generate_video(vehicle, script)
        
        # Fallback to secondary engine
        if not clip_paths_json:
            console.print(f"[yellow]  Falling back to Sora...[/yellow]")
            clip_paths_json = await sora.generate_video(vehicle, script)
        
        if not clip_paths_json:
            console.print(f"[red]  ✗ All engines failed for {cg_id}[/red]")
            update_vehicle_status(vehicle["id"], "error", error_message="All video engines failed")
            continue
        
        # Stitch clips into final video
        final_path = stitcher.stitch_clips(clip_paths_json, cg_id)
        
        if final_path:
            update_vehicle_status(
                vehicle["id"],
                "video_complete",
                video_path=final_path,
                video_cost=cost_tracker.session_cost,
                video_generated_at=datetime.now().isoformat(),
            )
            console.print(f"[bold green]  ✓ Complete: {final_path}[/bold green]")
        else:
            update_vehicle_status(vehicle["id"], "error", error_message="Stitching failed")
    
    cost_tracker.print_summary()


@click.command()
@click.option("--step", type=click.Choice(["scrape", "download", "scripts", "videos", "status", "all"]),
              default="all", help="Pipeline step to run")
@click.option("--max", "max_vehicles", type=int, default=0, help="Max vehicles to process (0 = all)")
@click.option("--quality", type=click.Choice(["fast", "standard", "pro"]),
              default=None, help="Video quality override")
@click.option("--retry", "retry_errors", is_flag=True, default=False,
              help="Reset failed vehicles to 'scraped' and re-process them")
def main(step: str, max_vehicles: int, quality: str, retry_errors: bool):
    """Run the CarGurus Vehicle Video Pipeline."""
    print_banner()

    # Override settings if provided
    if max_vehicles > 0:
        settings.MAX_VEHICLES = max_vehicles
    if quality:
        settings.VIDEO_QUALITY = quality
    
    # Validate config
    if step != "status":
        errors = settings.validate_config()
        if errors:
            for e in errors:
                console.print(f"[red]⚠ Config error: {e}[/red]")
            console.print("\n[yellow]Copy .env.example to .env and fill in your API keys[/yellow]")
            return
    
    # Initialize database
    init_db()

    if step == "status":
        print_status()
        return

    # Retry failed vehicles if requested
    if retry_errors:
        count = retry_failed_vehicles()
        if count:
            console.print(f"[green]✓ Reset {count} failed vehicle(s) for retry[/green]")
        else:
            console.print("[yellow]No failed vehicles to retry[/yellow]")

    async def run_pipeline():
        if step in ("scrape", "all"):
            console.print("\n[bold]═══ Step 1: Scraping CarGurus Inventory ═══[/bold]")
            await run_scraper(max_vehicles=max_vehicles)
        
        if step in ("download", "all"):
            console.print("\n[bold]═══ Step 2: Downloading Photos & Stickers ═══[/bold]")
            await run_downloader()
        
        if step in ("scripts", "all"):
            console.print("\n[bold]═══ Step 3: Generating Video Scripts ═══[/bold]")
            await run_script_generator()
        
        if step in ("videos", "all"):
            console.print("\n[bold]═══ Step 4: Generating Videos ═══[/bold]")
            await run_video_generation()
        
        console.print("\n")
        print_status()
    
    asyncio.run(run_pipeline())


if __name__ == "__main__":
    main()
