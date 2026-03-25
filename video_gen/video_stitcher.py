"""Video stitcher to combine generated clips into final 15-second videos.

Combines two 8-second Veo clips or 10+8-second Sora clips into a single
15-second output, trimming as needed and adding transitions.
"""

import json
import subprocess
from pathlib import Path

from rich.console import Console

from config import settings

console = Console()

TARGET_DURATION = settings.TARGET_VIDEO_DURATION  # 15 seconds


class VideoStitcher:
    """Combines video clips into final output videos."""
    
    def __init__(self):
        self._check_ffmpeg()
    
    def _check_ffmpeg(self):
        """Verify ffmpeg is available."""
        try:
            subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            console.print("[red]⚠ ffmpeg not found! Install it: sudo apt install ffmpeg[/red]")
            raise RuntimeError("ffmpeg is required for video stitching")
    
    def stitch_clips(self, clip_paths_json: str, output_name: str) -> str | None:
        """
        Combine video clips into a single 15-second video.
        
        Args:
            clip_paths_json: JSON string of clip file paths
            output_name: Base name for the output file
            
        Returns:
            Path to the final stitched video
        """
        clip_paths = json.loads(clip_paths_json)
        
        if not clip_paths:
            return None
        
        # Filter to existing files
        existing_clips = [p for p in clip_paths if Path(p).exists()]
        
        if not existing_clips:
            console.print(f"[red]No valid clip files found for {output_name}[/red]")
            return None
        
        output_path = settings.VIDEOS_DIR / f"{output_name}_final.mp4"
        
        if len(existing_clips) == 1:
            # Single clip — just trim to target duration
            return self._trim_to_duration(existing_clips[0], str(output_path))
        
        # Multiple clips — concatenate and trim
        return self._concat_and_trim(existing_clips, str(output_path))
    
    def _trim_to_duration(self, input_path: str, output_path: str) -> str | None:
        """Trim a single video to the target duration."""
        try:
            cmd = [
                "ffmpeg", "-y",
                "-i", input_path,
                "-t", str(TARGET_DURATION),
                "-c:v", "libx264",
                "-c:a", "aac",
                "-preset", "fast",
                "-crf", "23",
                output_path,
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                console.print(f"[green]    ✓ Final video: {Path(output_path).name}[/green]")
                return output_path
            else:
                console.print(f"[red]    ✗ Trim failed: {result.stderr[:200]}[/red]")
                return None
                
        except Exception as e:
            console.print(f"[red]    ✗ Trim error: {e}[/red]")
            return None
    
    def _concat_and_trim(self, clip_paths: list[str], output_path: str) -> str | None:
        """Concatenate multiple clips with crossfade and trim to target duration."""
        try:
            # Create a concat file list for ffmpeg
            concat_file = settings.VIDEOS_DIR / "_concat_list.txt"
            with open(concat_file, "w") as f:
                for clip in clip_paths:
                    f.write(f"file '{clip}'\n")
            
            # First pass: concatenate
            temp_concat = settings.VIDEOS_DIR / "_temp_concat.mp4"
            
            cmd_concat = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-c:v", "libx264",
                "-c:a", "aac",
                "-preset", "fast",
                str(temp_concat),
            ]
            
            result = subprocess.run(cmd_concat, capture_output=True, text=True)
            
            if result.returncode != 0:
                console.print(f"[red]    ✗ Concat failed: {result.stderr[:200]}[/red]")
                return None
            
            # Second pass: trim to exact duration and add slight crossfade
            cmd_trim = [
                "ffmpeg", "-y",
                "-i", str(temp_concat),
                "-t", str(TARGET_DURATION),
                "-vf", f"fade=t=in:st=0:d=0.5,fade=t=out:st={TARGET_DURATION - 0.5}:d=0.5",
                "-af", f"afade=t=in:st=0:d=0.5,afade=t=out:st={TARGET_DURATION - 0.5}:d=0.5",
                "-c:v", "libx264",
                "-c:a", "aac",
                "-preset", "fast",
                "-crf", "23",
                output_path,
            ]
            
            result = subprocess.run(cmd_trim, capture_output=True, text=True)
            
            # Cleanup temp files
            concat_file.unlink(missing_ok=True)
            temp_concat.unlink(missing_ok=True)
            
            if result.returncode == 0:
                console.print(f"[green]    ✓ Final video: {Path(output_path).name}[/green]")
                return output_path
            else:
                console.print(f"[red]    ✗ Final trim failed: {result.stderr[:200]}[/red]")
                return None
                
        except Exception as e:
            console.print(f"[red]    ✗ Stitch error: {e}[/red]")
            return None
