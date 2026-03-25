"""Text-to-speech greeting generation using OpenAI TTS API."""

import subprocess
from pathlib import Path

from openai import OpenAI

from config import settings
from utils.logger import get_logger

logger = get_logger("tts")


def generate_greeting_audio(
    client_name: str,
    person_name: str | None,
    output_dir: str | Path,
    voice: str = "onyx",
) -> str | None:
    """Generate a TTS audio file for the personalized greeting.

    Args:
        client_name: The client's name to greet
        person_name: The presenter's name (or None for generic)
        output_dir: Directory to save the audio file
        voice: OpenAI TTS voice (alloy, ash, coral, echo, fable, onyx, nova, sage, shimmer)

    Returns:
        Path to the generated MP3 file, or None on failure
    """
    presenter = person_name or "your sales representative"
    greeting_text = f"Hi {client_name}, I'm {presenter} with San Antonio Dodge."

    output_path = Path(output_dir) / "greeting.mp3"

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.audio.speech.create(
            model="tts-1-hd",
            voice=voice,
            input=greeting_text,
            speed=1.0,
        )
        response.stream_to_file(str(output_path))
        logger.info("TTS greeting generated: %s (%s)", output_path.name, greeting_text)
        return str(output_path)
    except Exception as e:
        logger.error("TTS greeting generation failed: %s: %s", type(e).__name__, e)
        return None


def mix_greeting_audio(
    video_path: str,
    greeting_audio_path: str,
    output_path: str,
) -> str | None:
    """Mix the TTS greeting audio into the beginning of the video.

    The greeting plays over the first few seconds; the rest of the video
    remains silent (or keeps its original audio if present).

    Args:
        video_path: Path to the input video
        greeting_audio_path: Path to the greeting MP3
        output_path: Path for the output video with audio

    Returns:
        Path to the output video, or None on failure
    """
    try:
        # Use FFmpeg to mix greeting audio at the start of the video.
        # The filter chain:
        # 1. Generate silent audio matching the video duration (anullsrc)
        # 2. Overlay the greeting audio at the beginning
        # 3. Map the original video stream unchanged
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", greeting_audio_path,
            "-filter_complex",
            # Create a silent audio stream matching video duration,
            # then mix the greeting audio on top of it
            "[0:a]apad=whole_dur=0[orig_a];"
            "[1:a]apad=pad_dur=0[greet];"
            "anullsrc=channel_layout=stereo:sample_rate=44100[silence];"
            "[silence][greet]amix=inputs=2:duration=longest:normalize=0[mixed];"
            "[mixed]atrim=0:duration=30[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path,
        ]

        # Simpler approach: just add the greeting audio to the video
        # (works whether or not the video already has audio)
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", greeting_audio_path,
            "-filter_complex",
            # Pad greeting audio with silence to match full video length
            "anullsrc=channel_layout=stereo:sample_rate=44100:duration=30[silence];"
            "[silence][1:a]amix=inputs=2:duration=first:normalize=0[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            output_path,
        ]

        logger.info("Mixing greeting audio into video: %s", Path(output_path).name)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if result.returncode != 0:
            logger.error("FFmpeg audio mix failed: %s", result.stderr[-500:])
            return None

        logger.info("Greeting audio mixed successfully: %s", Path(output_path).name)
        return output_path

    except Exception as e:
        logger.error("Audio mixing failed: %s: %s", type(e).__name__, e)
        return None
