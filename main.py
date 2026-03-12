#!/usr/bin/env python3
"""
CarGurus Vehicle Video Pipeline — Upload-First Workflow
========================================================
Users upload photos + window sticker + Carfax. The pipeline:
  1. Gemini multimodal extracts vehicle details + generates video script
  2. Veo/Sora generates a single cinematic 8-second clip
  3. FFmpeg composites intro + clip + CTA outro with branding overlays

Usage:
    python main.py upload <photo1> <photo2> ... [--sticker FILE] [--carfax FILE]
    python main.py status
    python main.py serve                     # Start web dashboard
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from config import settings
from utils.database import (
    init_db, get_all_vehicles, get_pipeline_stats,
    upsert_vehicle, update_vehicle_status,
)
from utils.cost_tracker import CostTracker
from scripts.multimodal_extractor import MultimodalExtractor
from video_gen.veo_generator import VeoGenerator
from video_gen.sora_generator import SoraGenerator
from video_gen.overlay import VideoOverlayPipeline

console = Console()


def print_banner():
    console.print(Panel.fit(
        "[bold cyan]CarGurus Vehicle Video Pipeline[/bold cyan]\n"
        f"[dim]Dealer: {settings.DEALER_NAME}[/dim]\n"
        f"[dim]Engine: {settings.PRIMARY_VIDEO_ENGINE.upper()} ({settings.VIDEO_QUALITY}) "
        f"| Budget: ${settings.COST_LIMIT:.2f}[/dim]",
        border_style="cyan",
    ))


def print_status():
    stats = get_pipeline_stats()
    table = Table(title="Pipeline Status")
    table.add_column("Status", style="cyan")
    table.add_column("Count", style="white", justify="right")

    status_order = [
        ("script_generated", "Script Generated"),
        ("video_generating", "Video Generating"),
        ("video_complete", "Video Complete"),
        ("error", "Error"),
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


@click.group()
def cli():
    """CarGurus Vehicle Video Pipeline — Upload-First Workflow"""
    pass


@cli.command()
@click.argument("photos", nargs=-1, type=click.Path(exists=True), required=True)
@click.option("--sticker", type=click.Path(exists=True), default=None, help="Window sticker image/PDF")
@click.option("--carfax", type=click.Path(exists=True), default=None, help="Carfax report image/PDF")
@click.option("--quality", type=click.Choice(["fast", "standard", "pro"]), default=None)
@click.option("--phone", default=None, help="Dealer phone number for overlay")
@click.option("--address", default=None, help="Dealer address for overlay")
@click.option("--cta", default=None, help="Call-to-action text")
def upload(photos, sticker, carfax, quality, phone, address, cta):
    """Upload vehicle photos and generate a branded video.

    Example:
        python main.py upload photo1.jpg photo2.jpg --sticker sticker.jpg --carfax carfax.pdf
    """
    print_banner()
    init_db()

    if quality:
        settings.VIDEO_QUALITY = quality

    errors = settings.validate_config()
    if errors:
        for e in errors:
            console.print(f"[red]Config error: {e}[/red]")
        console.print("\n[yellow]Copy .env.example to .env and fill in your API keys[/yellow]")
        return

    # Collect all image paths
    all_paths = list(photos)
    if sticker:
        all_paths.append(sticker)
    if carfax:
        all_paths.append(carfax)

    photo_paths = list(photos)

    console.print(f"\n[bold]Step 1: Analyzing {len(all_paths)} images with Gemini...[/bold]")

    extractor = MultimodalExtractor()
    result = extractor.extract_and_script(all_paths)

    if not result:
        console.print("[red]Failed to extract vehicle details. Check your images and API key.[/red]")
        return

    vehicle_info = result.get("vehicle", {})
    script_info = result.get("script", {})
    photo_analysis = result.get("photo_analysis", {})
    carfax_info = result.get("carfax", {})

    year = vehicle_info.get("year", "")
    make = vehicle_info.get("make", "")
    model = vehicle_info.get("model", "")
    trim = vehicle_info.get("trim", "")
    vehicle_name = f"{year} {make} {model} {trim}".strip()
    price = vehicle_info.get("price")

    console.print(f"[green]Identified: {vehicle_name}[/green]")
    if price:
        console.print(f"[green]Price: ${price:,.0f}[/green]")
    if carfax_info.get("accidents"):
        console.print(f"[green]Carfax: {carfax_info['accidents']}[/green]")

    # Save to database
    upload_id = f"cli_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    vehicle_data = {
        "cargurus_id": upload_id,
        "vin": vehicle_info.get("vin") or "",
        "year": vehicle_info.get("year") or 0,
        "make": make,
        "model": model,
        "trim": trim,
        "price": price or 0,
        "mileage": vehicle_info.get("mileage") or 0,
        "exterior_color": vehicle_info.get("exterior_color") or "",
        "interior_color": vehicle_info.get("interior_color") or "",
        "engine": vehicle_info.get("engine") or "",
        "transmission": vehicle_info.get("transmission") or "",
        "drivetrain": vehicle_info.get("drivetrain") or "",
        "photo_paths": json.dumps(photo_paths),
        "sticker_path": sticker or "",
        "video_script": json.dumps(result),
        "status": "script_generated",
        "script_generated_at": datetime.now().isoformat(),
    }
    vehicle_id = upsert_vehicle(vehicle_data)

    # Step 2: Generate video clip
    console.print(f"\n[bold]Step 2: Generating AI video clip...[/bold]")

    veo_prompt = script_info.get("veo_prompt", "")
    if not veo_prompt:
        console.print("[red]No video prompt generated[/red]")
        return

    best_idx = photo_analysis.get("best_exterior_index", 0)
    hero_photo = photo_paths[best_idx] if best_idx < len(photo_paths) else photo_paths[0]

    clip_path = None
    engine_used = "veo"

    async def generate():
        nonlocal clip_path, engine_used

        if settings.PRIMARY_VIDEO_ENGINE == "veo":
            veo = VeoGenerator()
            clip_path = await veo.generate_clip(veo_prompt, hero_photo, upload_id)

        if not clip_path:
            console.print("[yellow]Trying Sora fallback...[/yellow]")
            engine_used = "sora"
            sora = SoraGenerator()
            clip_path = await sora.generate_clip(veo_prompt, hero_photo, upload_id)

    update_vehicle_status(vehicle_id, "video_generating", video_engine=engine_used)
    asyncio.run(generate())

    if not clip_path:
        console.print("[red]All video engines failed[/red]")
        update_vehicle_status(vehicle_id, "error", error_message="All video engines failed")
        return

    # Step 3: Overlay pipeline
    console.print(f"\n[bold]Step 3: Adding branding and overlays...[/bold]")

    overlay = VideoOverlayPipeline()
    final_path = overlay.compose_final_video(
        ai_clip_path=clip_path,
        hero_photo_path=hero_photo,
        vehicle_name=vehicle_name,
        price=price,
        output_name=upload_id,
        dealer_phone=phone or "",
        dealer_address=address or "",
        dealer_logo_path=settings.DEALER_LOGO_PATH,
        cta_text=cta or "",
    )

    if not final_path:
        console.print("[red]Overlay compositing failed[/red]")
        update_vehicle_status(vehicle_id, "error", error_message="Overlay compositing failed")
        return

    cost_tracker = CostTracker()
    update_vehicle_status(
        vehicle_id,
        "video_complete",
        video_path=final_path,
        video_engine=engine_used,
        video_cost=cost_tracker.session_cost,
        video_generated_at=datetime.now().isoformat(),
    )

    console.print(f"\n[bold green]Done! Final video: {final_path}[/bold green]")

    caption = script_info.get("caption", "")
    if caption:
        console.print(f"\n[cyan]Social caption:[/cyan] {caption}")

    print_status()


@cli.command()
def status():
    """Show pipeline status."""
    print_banner()
    init_db()
    print_status()


@cli.command()
@click.option("--port", default=8080, help="Port number")
@click.option("--debug", is_flag=True, default=False)
def serve(port, debug):
    """Start the web dashboard."""
    from app import app as flask_app
    print_banner()
    console.print(f"[cyan]Starting web dashboard on http://localhost:{port}[/cyan]")
    flask_app.run(host="0.0.0.0", port=port, debug=debug)


if __name__ == "__main__":
    cli()
