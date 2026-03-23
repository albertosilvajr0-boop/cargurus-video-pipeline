"""FFmpeg-based video overlay pipeline.

Composites the final video from:
  1. Branded intro (2s) — static hero photo with dealer logo + vehicle name
  2. AI-generated clip (20s) — from Sora 2
  3. CTA outro (5s) — price, phone number, address, call-to-action

Text overlays can also be burned directly onto the video using FFmpeg drawtext.
No external services needed.
"""

import json
import shutil
import subprocess
import traceback
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from rich.console import Console

from config import settings
from utils.logger import get_logger

console = Console()
logger = get_logger("overlay")

# Font file mapping for FFmpeg drawtext filter
FONT_FILES = {
    "sans-serif": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "sans-serif-bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "serif": "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "serif-bold": "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "monospace": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "monospace-bold": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "liberation": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "liberation-bold": "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
}

# Video dimensions for 9:16 vertical
DIMENSIONS = {
    "720p": {"9:16": (720, 1280), "16:9": (1280, 720)},
    "1080p": {"9:16": (1080, 1920), "16:9": (1920, 1080)},
}


class VideoOverlayPipeline:
    """Produces final branded videos with intro, AI clip, and CTA outro."""

    def __init__(self):
        self._check_ffmpeg()
        res = settings.VIDEO_RESOLUTION
        aspect = settings.VIDEO_ASPECT_RATIO
        self.width, self.height = DIMENSIONS.get(res, DIMENSIONS["720p"]).get(
            aspect, DIMENSIONS["720p"]["9:16"]
        )
        self.fps = 30

    def _check_ffmpeg(self):
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError("ffmpeg is required — install it: sudo apt install ffmpeg")

    def extract_frame(self, video_path: str, time_sec: float = 1.0) -> str | None:
        """Extract a single frame from a video at the given timestamp.

        Returns path to the extracted PNG frame, or None on failure.
        """
        frame_path = str(settings.VIDEOS_DIR / f"_preview_frame_{Path(video_path).stem}.jpg")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(time_sec),
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            frame_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("Frame extraction failed: %s", result.stderr[:300])
            return None
        return frame_path

    def _prepare_image_overlay(self, image_path: str, width: int, height: int, opacity: float = 1.0) -> str | None:
        """Prepare an image overlay: resize to target dimensions and apply opacity.

        Returns path to the prepared PNG image, or None on failure.
        """
        try:
            img = Image.open(image_path).convert("RGBA")
            img = img.resize((width, height), Image.LANCZOS)

            if opacity < 1.0:
                # Apply opacity by modifying alpha channel
                r, g, b, a = img.split()
                a = a.point(lambda x: int(x * opacity))
                img = Image.merge("RGBA", (r, g, b, a))

            prepared_path = str(settings.VIDEOS_DIR / f"_overlay_img_{Path(image_path).stem}_{width}x{height}.png")
            img.save(prepared_path, "PNG")
            return prepared_path
        except Exception as e:
            logger.error("Failed to prepare image overlay: %s", e)
            return None

    def apply_overlays(
        self,
        video_path: str,
        overlays: list[dict],
        output_path: str,
        progress_callback: "callable | None" = None,
    ) -> str | None:
        """Burn text and image overlays directly onto the video.

        Each overlay dict should contain:
          - type: str — "text" or "image"

        For type="text":
          - text: str — the text to display
          - x: float — x position as fraction of video width (0.0-1.0)
          - y: float — y position as fraction of video height (0.0-1.0)
          - fontSize: int — font size in pixels
          - fontColor: str — hex color like "#ffffff"
          - fontFamily: str — font family key (sans-serif, serif, monospace, liberation)
          - bold: bool — whether to use bold variant
          - backgroundColor: str — optional background color hex (empty = transparent)
          - backgroundOpacity: float — background opacity 0.0-1.0
          - shadowColor: str — shadow color hex (default "#000000")
          - shadowX: int — shadow x offset (default 2)
          - shadowY: int — shadow y offset (default 2)
          - startTime: float — when to start showing (seconds, default 0)
          - endTime: float — when to stop showing (seconds, default=video duration)

        For type="image":
          - imagePath: str — path to the image file on disk
          - x: float — x position as fraction (0.0-1.0)
          - y: float — y position as fraction (0.0-1.0)
          - width: int — display width in video pixels
          - height: int — display height in video pixels
          - opacity: float — image opacity 0.0-1.0 (default 1.0)
          - startTime: float (optional)
          - endTime: float (optional)

        Returns path to output video or None on failure.
        """
        if not overlays:
            logger.info("No overlays to apply, copying video as-is")
            shutil.copy2(video_path, output_path)
            return output_path

        def _progress(pct, msg):
            if progress_callback:
                progress_callback(pct, msg)

        _progress(20, "Building overlay filters...")

        # Separate text and image overlays
        text_overlays = [ov for ov in overlays if ov.get("type", "text") == "text"]
        image_overlays = [ov for ov in overlays if ov.get("type") == "image"]

        # Build drawtext filter chain for text overlays
        drawtext_filters = []
        for ov in text_overlays:
            text = ov.get("text", "").replace("'", "\u2019").replace(":", "\\:")
            if not text:
                continue

            family = ov.get("fontFamily", "sans-serif")
            bold = ov.get("bold", False)
            font_key = f"{family}-bold" if bold else family
            font_file = FONT_FILES.get(font_key, FONT_FILES.get(family, FONT_FILES["sans-serif"]))
            if not Path(font_file).exists():
                font_file = FONT_FILES["sans-serif"]

            font_size = int(ov.get("fontSize", 36))
            font_color = ov.get("fontColor", "#ffffff")

            x_frac = float(ov.get("x", 0.5))
            y_frac = float(ov.get("y", 0.5))
            x_expr = f"(main_w*{x_frac:.4f})"
            y_expr = f"(main_h*{y_frac:.4f})"

            shadow_color = ov.get("shadowColor", "#000000")
            shadow_x = int(ov.get("shadowX", 2))
            shadow_y = int(ov.get("shadowY", 2))

            bg_color = ov.get("backgroundColor", "")
            bg_opacity = float(ov.get("backgroundOpacity", 0))

            start_time = float(ov.get("startTime", 0))
            end_time = ov.get("endTime")

            parts = [
                f"fontfile='{font_file}'",
                f"text='{text}'",
                f"fontsize={font_size}",
                f"fontcolor={font_color}",
                f"x={x_expr}",
                f"y={y_expr}",
                f"shadowcolor={shadow_color}",
                f"shadowx={shadow_x}",
                f"shadowy={shadow_y}",
            ]

            if bg_color and bg_opacity > 0:
                parts.append("box=1")
                parts.append(f"boxcolor={bg_color}@{bg_opacity:.2f}")
                parts.append("boxborderw=8")

            if start_time > 0 or end_time is not None:
                conditions = []
                if start_time > 0:
                    conditions.append(f"gte(t,{start_time:.2f})")
                if end_time is not None:
                    conditions.append(f"lte(t,{float(end_time):.2f})")
                enable_expr = "*".join(conditions)
                parts.append(f"enable='{enable_expr}'")

            drawtext_filters.append("drawtext=" + ":".join(parts))

        # Prepare image overlays — each needs a separate input and overlay filter
        prepared_images = []  # list of (prepared_path, x_expr, y_expr, enable_expr)
        cleanup_paths = []
        for ov in image_overlays:
            img_path = ov.get("imagePath", "")
            if not img_path or not Path(img_path).exists():
                logger.warning("Image overlay path not found: %s", img_path)
                continue

            img_w = int(ov.get("width", 100))
            img_h = int(ov.get("height", 100))
            opacity = float(ov.get("opacity", 1.0))

            prepared = self._prepare_image_overlay(img_path, img_w, img_h, opacity)
            if not prepared:
                continue
            cleanup_paths.append(prepared)

            x_frac = float(ov.get("x", 0.5))
            y_frac = float(ov.get("y", 0.5))
            x_expr = f"(main_w*{x_frac:.4f})"
            y_expr = f"(main_h*{y_frac:.4f})"

            start_time = float(ov.get("startTime", 0))
            end_time = ov.get("endTime")
            enable_expr = None
            if start_time > 0 or end_time is not None:
                conditions = []
                if start_time > 0:
                    conditions.append(f"gte(t,{start_time:.2f})")
                if end_time is not None:
                    conditions.append(f"lte(t,{float(end_time):.2f})")
                enable_expr = "*".join(conditions)

            prepared_images.append((prepared, x_expr, y_expr, enable_expr))

        total_overlays = len(drawtext_filters) + len(prepared_images)
        if total_overlays == 0:
            shutil.copy2(video_path, output_path)
            return output_path

        _progress(40, f"Applying {total_overlays} overlay(s)...")

        # Build FFmpeg command
        # Strategy: image overlays need extra -i inputs and [overlay] filter chain,
        # then drawtext filters are appended at the end.
        cmd = ["ffmpeg", "-y", "-i", video_path]

        # Add image inputs
        for prepared_path, _, _, _ in prepared_images:
            cmd.extend(["-i", prepared_path])

        # Build filter_complex for image overlays + drawtext
        if prepared_images:
            # Build a filter_complex chain
            filter_parts = []
            current_label = "[0:v]"

            for i, (_, x_expr, y_expr, enable_expr) in enumerate(prepared_images):
                input_idx = i + 1  # 0 is the video
                out_label = f"[ov{i}]"
                overlay_filter = f"{current_label}[{input_idx}:v]overlay=x={x_expr}:y={y_expr}"
                if enable_expr:
                    overlay_filter += f":enable='{enable_expr}'"
                overlay_filter += out_label
                filter_parts.append(overlay_filter)
                current_label = out_label

            # Append drawtext filters to the last output
            if drawtext_filters:
                # Strip the label from last overlay output and chain drawtext
                dt_chain = ",".join(drawtext_filters)
                filter_parts.append(f"{current_label}{dt_chain}[final]")
                map_label = "[final]"
            else:
                map_label = current_label

            filter_complex = ";".join(filter_parts)
            cmd.extend(["-filter_complex", filter_complex, "-map", map_label, "-map", "0:a?"])
        else:
            # Text-only: simple -vf
            vf = ",".join(drawtext_filters)
            cmd.extend(["-vf", vf])

        cmd.extend([
            "-c:v", "libx264",
            "-c:a", "copy",
            "-preset", "fast",
            "-crf", "23",
            output_path,
        ])

        logger.info("FFmpeg overlay cmd: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)

        _progress(80, "Finalizing overlaid video...")

        # Clean up prepared image files
        for p in cleanup_paths:
            Path(p).unlink(missing_ok=True)

        if result.returncode != 0:
            logger.error("FFmpeg overlay failed (rc=%d): %s", result.returncode, result.stderr[:500])
            console.print(f"[red]Overlay error: {result.stderr[:300]}[/red]")
            return None

        _progress(95, "Overlays applied successfully")
        logger.info("Overlays applied: %s -> %s (%d text, %d image)",
                     video_path, output_path, len(drawtext_filters), len(prepared_images))
        return output_path

    # Keep backward-compatible alias
    def apply_text_overlays(self, video_path, overlays, output_path, progress_callback=None):
        """Backward-compatible wrapper — delegates to apply_overlays."""
        return self.apply_overlays(video_path, overlays, output_path, progress_callback)

    def recompose_overlay(
        self,
        vehicle_id_or_clip: str,
        vehicle_name: str | None = None,
        price: float | int | None = None,
        hero_photo_path: str | None = None,
        dealer_phone: str = "",
        dealer_address: str = "",
        dealer_logo_path: str = "",
        cta_text: str = "",
        vehicle_specs: dict | None = None,
        progress_callback: "callable | None" = None,
    ) -> str | None:
        """
        Re-apply overlays to an already-downloaded AI clip — zero API cost.

        Finds the original _clip.mp4, re-generates intro/outro frames with
        updated branding, and produces a new _final.mp4.

        Args:
            vehicle_id_or_clip: Either a vehicle cargurus_id (e.g. "vin_1C4RJFAG...")
                                or a direct path to a _clip.mp4 file.
            vehicle_name: Vehicle name for overlays (required if not loading from DB).
            price: Updated price (or None to omit).
            hero_photo_path: Path to hero photo (None for text-only intro).
            dealer_phone: Override phone number.
            dealer_address: Override address.
            dealer_logo_path: Override logo path.
            cta_text: Override CTA text.
            vehicle_specs: Optional specs dict for text-only intro.

        Returns:
            Path to the new final video, or None on failure.
        """
        # Resolve the clip path
        clip_path = None
        output_name = None

        if Path(vehicle_id_or_clip).exists() and vehicle_id_or_clip.endswith(".mp4"):
            # Direct path to clip file
            clip_path = vehicle_id_or_clip
            stem = Path(clip_path).stem
            output_name = stem.replace("_clip", "").replace("_final", "")
        else:
            # Treat as cargurus_id — look for the _clip.mp4 in the videos dir
            output_name = vehicle_id_or_clip
            candidate = settings.VIDEOS_DIR / f"{output_name}_clip.mp4"
            if candidate.exists():
                clip_path = str(candidate)
            else:
                logger.error("No clip file found for %s (looked for %s)", vehicle_id_or_clip, candidate)
                console.print(f"[red]No clip file found: {candidate}[/red]")
                console.print("[dim]The original AI clip (_clip.mp4) must exist to re-apply overlays.[/dim]")
                return None

        logger.info("Re-overlay starting — clip=%s, name=%s", clip_path, vehicle_name)
        console.print(
            f"[bold cyan]Re-applying overlays[/bold cyan] (local FFmpeg only — [green]$0 API cost[/green])"
        )

        if progress_callback:
            progress_callback(10, "Located clip file, starting overlay compose...")

        return self.compose_final_video(
            ai_clip_path=clip_path,
            hero_photo_path=hero_photo_path,
            vehicle_name=vehicle_name or output_name,
            price=price,
            output_name=output_name,
            dealer_phone=dealer_phone,
            dealer_address=dealer_address,
            dealer_logo_path=dealer_logo_path,
            cta_text=cta_text,
            vehicle_specs=vehicle_specs,
            progress_callback=progress_callback,
        )

    def compose_final_video(
        self,
        ai_clip_path: str,
        hero_photo_path: str | None,
        vehicle_name: str,
        price: float | int | None,
        output_name: str,
        dealer_phone: str = "",
        dealer_address: str = "",
        dealer_logo_path: str = "",
        cta_text: str = "",
        vehicle_specs: dict | None = None,
        progress_callback: "callable | None" = None,
    ) -> str | None:
        """
        Compose the final branded video.

        Structure: [2s intro] + [20s AI clip] + [5s CTA outro] = 25s

        Args:
            ai_clip_path: Path to the AI-generated video clip
            hero_photo_path: Path to the best exterior photo (None for VIN-only mode)
            vehicle_name: e.g. "2024 Jeep Grand Cherokee Trailhawk"
            price: Vehicle price (or None to omit)
            output_name: Base filename for output
            dealer_phone: Phone number to display
            dealer_address: Address to display
            dealer_logo_path: Path to dealer logo PNG
            cta_text: Call-to-action text
            vehicle_specs: Optional dict of vehicle specs for text-only intro

        Returns:
            Path to final video, or None on failure
        """
        logger.info(
            "Overlay compose starting — clip=%s, hero=%s, vehicle=%s",
            ai_clip_path, hero_photo_path, vehicle_name,
        )

        if not Path(ai_clip_path).exists():
            logger.error("AI clip file not found: %s", ai_clip_path)
            console.print(f"[red]AI clip not found: {ai_clip_path}[/red]")
            return None

        phone = dealer_phone or settings.DEALER_PHONE
        address = dealer_address or settings.DEALER_ADDRESS
        logo = dealer_logo_path or settings.DEALER_LOGO_PATH
        cta = cta_text or settings.OVERLAY_CTA_TEXT

        intro_path = settings.VIDEOS_DIR / f"{output_name}_intro.mp4"
        outro_path = settings.VIDEOS_DIR / f"{output_name}_outro.mp4"
        final_path = settings.VIDEOS_DIR / f"{output_name}_final.mp4"

        def _progress(pct, msg):
            if progress_callback:
                progress_callback(pct, msg)

        try:
            _progress(20, "Generating intro frame...")
            # Step 1: Generate intro frame and convert to 2s video
            # Skip photo-based intro — jump straight into AI clip.
            # Only generate a text-based intro for VIN-only mode (no hero photo).
            if hero_photo_path and Path(hero_photo_path).exists():
                console.print("[dim]Skipping photo intro — jumping straight to AI clip[/dim]")
                intro_path = None
            else:
                intro_frame = self._create_intro_frame(
                    None, vehicle_name, logo,
                    vehicle_specs=vehicle_specs,
                )
                if intro_frame:
                    if not self._image_to_video(intro_frame, str(intro_path), duration=2):
                        logger.warning("Intro video generation failed, skipping intro")
                        intro_path = None
                else:
                    console.print("[yellow]Skipping intro — could not create frame[/yellow]")
                    intro_path = None

            _progress(35, "Generating CTA outro frame...")
            # Step 2: Generate outro frame and convert to 5s video
            outro_frame = self._create_outro_frame(
                vehicle_name, price, phone, address, cta, logo
            )
            if outro_frame:
                if not self._image_to_video(outro_frame, str(outro_path), duration=5):
                    logger.error("Outro video generation failed — CTA overlay will be missing")
                    outro_path = None
            else:
                logger.error("Outro frame creation failed — CTA overlay will be missing")
                console.print("[yellow]Skipping outro — could not create frame[/yellow]")
                outro_path = None

            _progress(50, "Normalizing AI clip dimensions...")
            # Step 3: Normalize the AI clip to match our dimensions/fps
            normalized_clip = settings.VIDEOS_DIR / f"{output_name}_normalized.mp4"
            self._normalize_clip(ai_clip_path, str(normalized_clip))

            _progress(70, "Concatenating segments with transitions...")
            # Step 4: Concatenate intro + AI clip + outro with transitions
            segments = []
            if intro_path and intro_path.exists():
                segments.append(str(intro_path))
            segments.append(str(normalized_clip))
            if outro_path and outro_path.exists():
                segments.append(str(outro_path))

            result = self._concat_with_fades(segments, str(final_path))

            _progress(90, "Cleaning up temporary files...")
            # Cleanup temp files
            for tmp in [intro_path, outro_path, normalized_clip]:
                if tmp and Path(tmp).exists():
                    Path(tmp).unlink(missing_ok=True)
            # Clean up generated frames
            for frame_file in settings.VIDEOS_DIR.glob(f"{output_name}_*_frame.png"):
                frame_file.unlink(missing_ok=True)

            if result:
                logger.info("Final video composed: %s", final_path.name)
                console.print(f"[bold green]Final video: {final_path.name}[/bold green]")
            else:
                logger.error("Overlay concat failed — no output produced")
            return result

        except Exception as e:
            logger.error("Overlay pipeline error: %s: %s", type(e).__name__, e)
            logger.debug("Overlay traceback:\n%s", traceback.format_exc())
            console.print(f"[red]Overlay pipeline error: {e}[/red]")
            return None

    def _create_intro_frame(
        self, hero_photo_path: str | None, vehicle_name: str, logo_path: str,
        vehicle_specs: dict | None = None,
    ) -> str | None:
        """Create the intro frame.

        If hero_photo_path is provided: photo background with vehicle name overlay.
        If no photo (VIN-only mode): branded dark background with specs.
        """
        try:
            if hero_photo_path and Path(hero_photo_path).exists():
                # Photo-based intro
                img = Image.open(hero_photo_path).convert("RGBA")
                img = self._fit_image(img)
            else:
                # Text-only intro (VIN mode) — dark branded background
                img = Image.new("RGBA", (self.width, self.height), (15, 17, 23, 255))

            draw = ImageDraw.Draw(img)

            if hero_photo_path and Path(hero_photo_path).exists():
                # Semi-transparent gradient at bottom for photo readability
                gradient = Image.new("RGBA", (self.width, self.height // 3), (0, 0, 0, 0))
                grad_draw = ImageDraw.Draw(gradient)
                for y in range(gradient.height):
                    alpha = int(200 * (y / gradient.height))
                    grad_draw.rectangle([(0, y), (self.width, y + 1)], fill=(0, 0, 0, alpha))
                img.paste(gradient, (0, self.height - gradient.height), gradient)

                # Vehicle name text at bottom
                font = self._get_font(size=int(self.width * 0.055))
                text_y = self.height - int(self.height * 0.12)
                self._draw_centered_text(draw, vehicle_name, text_y, font, fill="white")
            else:
                # Text-only layout: logo at top, vehicle name centered, specs below
                y_cursor = int(self.height * 0.25)

                if logo_path and Path(logo_path).exists():
                    self._paste_logo(img, logo_path, position="center-top")
                    y_cursor = int(self.height * 0.38)

                # Vehicle name (large)
                name_font = self._get_font(size=int(self.width * 0.065), bold=True)
                self._draw_centered_text(draw, vehicle_name, y_cursor, name_font, fill="white")
                y_cursor += int(self.height * 0.08)

                # Specs lines
                if vehicle_specs:
                    spec_font = self._get_font(size=int(self.width * 0.04))
                    spec_lines = []
                    if vehicle_specs.get("engine"):
                        spec_lines.append(vehicle_specs["engine"])
                    if vehicle_specs.get("drivetrain"):
                        spec_lines.append(vehicle_specs["drivetrain"])
                    if vehicle_specs.get("body_style"):
                        spec_lines.append(vehicle_specs["body_style"])
                    for line in spec_lines[:3]:
                        self._draw_centered_text(draw, line, y_cursor, spec_font, fill="#a1a1aa")
                        y_cursor += int(self.height * 0.045)

                # Decorative line
                line_y = y_cursor + int(self.height * 0.02)
                line_w = int(self.width * 0.3)
                line_x = (self.width - line_w) // 2
                draw.rectangle(
                    [(line_x, line_y), (line_x + line_w, line_y + 2)],
                    fill="#3b82f6",
                )

            # Dealer logo in corner (photo mode) or already placed (text mode)
            if hero_photo_path and Path(hero_photo_path).exists():
                if logo_path and Path(logo_path).exists():
                    self._paste_logo(img, logo_path, position="top-left")

            safe_name = vehicle_name.replace(" ", "_").replace("/", "_")[:30]
            frame_path = str(settings.VIDEOS_DIR / f"{safe_name}_intro_frame.png")
            img.convert("RGB").save(frame_path, "PNG")
            return frame_path

        except Exception as e:
            console.print(f"[yellow]Intro frame error: {e}[/yellow]")
            return None

    def _create_outro_frame(
        self,
        vehicle_name: str,
        price: float | int | None,
        phone: str,
        address: str,
        cta_text: str,
        logo_path: str,
    ) -> str | None:
        """Create the CTA outro frame: dark background with price, contact info, CTA."""
        try:
            # Dark branded background
            img = Image.new("RGBA", (self.width, self.height), (15, 17, 23, 255))
            draw = ImageDraw.Draw(img)

            y_cursor = int(self.height * 0.2)

            # Dealer logo centered at top
            if logo_path and Path(logo_path).exists():
                self._paste_logo(img, logo_path, position="center-top")
                y_cursor = int(self.height * 0.35)

            # Vehicle name
            name_font = self._get_font(size=int(self.width * 0.05))
            self._draw_centered_text(draw, vehicle_name, y_cursor, name_font, fill="white")
            y_cursor += int(self.height * 0.07)

            # Price badge
            if price and price > 0:
                price_font = self._get_font(size=int(self.width * 0.09), bold=True)
                price_text = f"${price:,.0f}"
                self._draw_centered_text(draw, price_text, y_cursor, price_font, fill="#22c55e")
                y_cursor += int(self.height * 0.1)

            # CTA text
            if cta_text:
                cta_font = self._get_font(size=int(self.width * 0.06), bold=True)
                self._draw_centered_text(draw, cta_text, y_cursor, cta_font, fill="#3b82f6")
                y_cursor += int(self.height * 0.09)

            # Phone number
            if phone:
                phone_font = self._get_font(size=int(self.width * 0.065), bold=True)
                self._draw_centered_text(draw, phone, y_cursor, phone_font, fill="white")
                y_cursor += int(self.height * 0.08)

            # Address
            if address:
                addr_font = self._get_font(size=int(self.width * 0.035))
                self._draw_centered_text(draw, address, y_cursor, addr_font, fill="#a1a1aa")
                y_cursor += int(self.height * 0.06)

            # Dealer name at bottom
            dealer_font = self._get_font(size=int(self.width * 0.03))
            self._draw_centered_text(
                draw, settings.DEALER_NAME,
                self.height - int(self.height * 0.08),
                dealer_font, fill="#71717a"
            )

            frame_path = str(settings.VIDEOS_DIR / f"outro_frame.png")
            img.convert("RGB").save(frame_path, "PNG")
            return frame_path

        except Exception as e:
            console.print(f"[yellow]Outro frame error: {e}[/yellow]")
            return None

    def _fit_image(self, img: Image.Image) -> Image.Image:
        """Resize and crop image to fit target dimensions."""
        target_ratio = self.width / self.height
        img_ratio = img.width / img.height

        if img_ratio > target_ratio:
            # Image is wider — crop sides
            new_width = int(img.height * target_ratio)
            offset = (img.width - new_width) // 2
            img = img.crop((offset, 0, offset + new_width, img.height))
        else:
            # Image is taller — crop top/bottom
            new_height = int(img.width / target_ratio)
            offset = (img.height - new_height) // 2
            img = img.crop((0, offset, img.width, offset + new_height))

        return img.resize((self.width, self.height), Image.LANCZOS)

    def _get_font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        """Get a font, falling back to default if custom font not available."""
        if settings.OVERLAY_FONT and Path(settings.OVERLAY_FONT).exists():
            return ImageFont.truetype(settings.OVERLAY_FONT, size)

        # Try common system fonts
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
        for font_path in candidates:
            if Path(font_path).exists():
                return ImageFont.truetype(font_path, size)

        return ImageFont.load_default()

    def _draw_centered_text(
        self, draw: ImageDraw.Draw, text: str, y: int,
        font: ImageFont.FreeTypeFont, fill: str = "white"
    ):
        """Draw text centered horizontally."""
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        x = (self.width - text_width) // 2
        # Draw shadow for readability
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 180))
        draw.text((x, y), text, font=font, fill=fill)

    def _paste_logo(self, img: Image.Image, logo_path: str, position: str = "top-left"):
        """Paste a logo onto the image."""
        try:
            logo = Image.open(logo_path).convert("RGBA")
            # Scale logo to ~15% of frame width
            max_logo_w = int(self.width * 0.25)
            ratio = max_logo_w / logo.width
            logo = logo.resize(
                (int(logo.width * ratio), int(logo.height * ratio)),
                Image.LANCZOS,
            )

            margin = int(self.width * 0.04)
            if position == "top-left":
                pos = (margin, margin)
            elif position == "top-right":
                pos = (self.width - logo.width - margin, margin)
            elif position == "center-top":
                pos = ((self.width - logo.width) // 2, int(self.height * 0.08))
            else:
                pos = (margin, margin)

            img.paste(logo, pos, logo)
        except Exception as e:
            console.print(f"[yellow]Logo paste error: {e}[/yellow]")

    def _image_to_video(self, image_path: str, output_path: str, duration: float) -> bool:
        """Convert a static image to a video of given duration with subtle zoom.

        Generates a silent audio track so concat with audio clips works correctly.
        Returns True on success, False on failure.
        """
        # Use zoompan for a slow Ken Burns effect
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", image_path,
            "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            "-vf", (
                f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
                f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"zoompan=z='min(zoom+0.001,1.04)':x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':d={duration * self.fps}:s={self.width}x{self.height}:fps={self.fps}"
            ),
            "-preset", "fast",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("FFmpeg image-to-video failed (rc=%d): %s", result.returncode, result.stderr[:500])
            console.print(f"[red]Image-to-video error: {result.stderr[:300]}[/red]")
            return False
        logger.debug("FFmpeg image-to-video OK: %s -> %s", image_path, output_path)
        return True

    def _normalize_clip(self, input_path: str, output_path: str):
        """Normalize AI clip to match our dimensions, fps, and codec."""
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", (
                f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
                f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={self.fps}"
            ),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-preset", "fast",
            "-crf", "23",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning("FFmpeg normalize returned %d: %s", result.returncode, result.stderr[:500])
            console.print(f"[yellow]Normalize warning: {result.stderr[:200]}[/yellow]")
        else:
            logger.debug("FFmpeg normalize OK: %s -> %s", input_path, output_path)

    def _concat_with_fades(self, segment_paths: list[str], output_path: str) -> str | None:
        """Concatenate video segments with crossfade transitions."""
        if not segment_paths:
            return None

        if len(segment_paths) == 1:
            # Single segment — just copy
            shutil.copy2(segment_paths[0], output_path)
            return output_path

        # Write concat file
        concat_file = settings.VIDEOS_DIR / "_concat_list.txt"
        with open(concat_file, "w") as f:
            for seg in segment_paths:
                f.write(f"file '{seg}'\n")

        # Concatenate with crossfade at boundaries
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-t", str(settings.TARGET_VIDEO_DURATION),
            "-vf", (
                "fade=t=in:st=0:d=0.3,"
                f"fade=t=out:st={settings.TARGET_VIDEO_DURATION - 0.3}:d=0.3"
            ),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-preset", "fast",
            "-crf", "23",
            output_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        concat_file.unlink(missing_ok=True)

        if result.returncode == 0:
            logger.info("FFmpeg concat OK: %d segments -> %s", len(segment_paths), output_path)
            return output_path
        else:
            logger.error("FFmpeg concat failed (rc=%d): %s", result.returncode, result.stderr[:500])
            console.print(f"[red]Concat failed: {result.stderr[:300]}[/red]")
            return None
