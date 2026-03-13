"""Multimodal vehicle extraction and script generation.

Sends uploaded photos, window sticker, and Carfax to Gemini 2.0 Flash
in a single call. Gemini OCRs the documents, analyzes the photos, extracts
all vehicle details, and generates a video script — replacing the old
scrape → download → script pipeline.
"""

import json
import re
from pathlib import Path

from google import genai
from google.genai import types
from rich.console import Console

from config import settings
from utils.retry import retry_sync

console = Console()

EXTRACTION_PROMPT = """You are an expert automotive analyst and video scriptwriter.

I am uploading images for a single vehicle. The images include:
- Vehicle photos (exterior and/or interior shots)
- A window sticker (Monroney sticker) — if provided
- A Carfax vehicle history report — if provided

## Task 1: Extract Vehicle Details

From the window sticker and photos, extract as much as possible:
- Year, Make, Model, Trim
- MSRP / Sticker Price
- Exterior Color, Interior Color
- Engine (displacement, cylinders, horsepower if shown)
- Transmission
- Drivetrain (FWD, RWD, AWD, 4WD)
- Key packages and options (from sticker)
- VIN (from sticker if visible)

From the Carfax (if provided), extract:
- Number of previous owners
- Accident history (clean or incidents)
- Service record highlights

From the photos, identify:
- Which photo is the best exterior hero shot (index number, 0-based)
- Which photo best shows the interior (index number)
- Notable visual features (wheels, paint finish, body style, roof type)
- Overall condition impression

## Task 2: Generate Video Script

Create a compelling 15-second video script for {dealer_name}'s social media.
The final video will be: [2s branded intro] + [8s AI-generated cinematic clip] + [5s CTA outro].

You are writing the prompt for the 8-second AI video generation clip.

## Respond with this exact JSON structure:

{{
    "vehicle": {{
        "year": 2024,
        "make": "Jeep",
        "model": "Grand Cherokee",
        "trim": "Trailhawk",
        "price": 52990,
        "msrp": 55490,
        "exterior_color": "Midnight Blue",
        "interior_color": "Black Leather",
        "engine": "3.6L V6 293hp",
        "transmission": "8-Speed Automatic",
        "drivetrain": "4WD",
        "vin": "1C4RJFAG...",
        "mileage": 0,
        "packages": ["Trailhawk Package", "Advanced ProTech Group"],
        "condition_notes": "New, excellent condition"
    }},
    "carfax": {{
        "owners": 0,
        "accidents": "Clean - no accidents reported",
        "service_highlights": "N/A - new vehicle"
    }},
    "photo_analysis": {{
        "best_exterior_index": 0,
        "best_interior_index": 3,
        "visual_highlights": "Aggressive Trailhawk styling, red tow hooks, all-terrain tires, panoramic sunroof"
    }},
    "script": {{
        "hook": "Built for where the road ends.",
        "veo_prompt": "Cinematic reveal of a Midnight Blue 2024 Jeep Grand Cherokee Trailhawk on a dramatic desert trail at golden hour. Camera starts low behind the rear quarter panel, slowly dollying around to reveal the aggressive front grille with red tow hooks. Dust particles float in warm amber sunlight. The camera rises smoothly showing the vehicle's commanding stance against a vast southwestern landscape. Shallow depth of field with lens flares. Premium automotive commercial quality, 4K cinematic color grading.",
        "text_overlay": "$52,990 | Grand Cherokee Trailhawk",
        "cta": "Call {dealer_name} today!",
        "caption": "Where the road ends, adventure begins. 2024 Grand Cherokee Trailhawk now available. #Jeep #GrandCherokee #Trailhawk #4x4 #Adventure",
        "target_emotion": "adventure"
    }}
}}

## Guidelines
- The veo_prompt is the MOST IMPORTANT field — make it extremely cinematic and detailed
- Reference the actual color and vehicle from the photos
- Match the emotion to the vehicle type (truck=rugged, sedan=elegant, sports=excitement)
- Keep text_overlay under 40 characters — it will be burned onto the video
- Fill in all vehicle fields you can extract; use null for anything not visible
- If no window sticker or Carfax is provided, extract what you can from the photos alone

Respond ONLY with the JSON object. No markdown code fences.
"""


class MultimodalExtractor:
    """Extracts vehicle data and generates scripts from uploaded images."""

    def __init__(self):
        self.client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        self.model_name = "gemini-2.5-flash"

    def extract_and_script(self, image_paths: list[str]) -> dict | None:
        """
        Send all uploaded images to Gemini in one call.

        Args:
            image_paths: List of file paths (photos, sticker, carfax)

        Returns:
            Parsed JSON dict with vehicle details and script, or None on failure
        """
        if not image_paths:
            console.print("[red]No images provided[/red]")
            return None

        # Build multimodal content parts
        parts = []
        for path_str in image_paths:
            path = Path(path_str)
            if not path.exists():
                continue

            mime = self._get_mime_type(path)
            image_bytes = path.read_bytes()
            parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime))

        if not parts:
            console.print("[red]No valid image files found[/red]")
            return None

        # Add the text prompt
        prompt_text = EXTRACTION_PROMPT.format(dealer_name=settings.DEALER_NAME)
        parts.append(types.Part.from_text(text=prompt_text))

        console.print(f"[cyan]Sending {len(parts) - 1} images to Gemini for analysis...[/cyan]")

        response = self._call_gemini(parts)
        return self._parse_response(response)

    @retry_sync(max_retries=3, base_delay=2.0, operation_name="Gemini multimodal extraction")
    def _call_gemini(self, parts: list):
        """Call Gemini with multimodal content."""
        return self.client.models.generate_content(
            model=self.model_name,
            contents=types.Content(parts=parts),
            config=types.GenerateContentConfig(
                temperature=0.7,
                max_output_tokens=3000,
            ),
        )

    def _parse_response(self, response) -> dict | None:
        """Parse JSON from Gemini response."""
        text = response.text.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
            console.print(f"[red]Failed to parse Gemini response as JSON[/red]")
            return None

    def _get_mime_type(self, path: Path) -> str:
        """Determine MIME type from file extension."""
        suffix = path.suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".pdf": "application/pdf",
            ".bmp": "image/bmp",
        }
        return mime_map.get(suffix, "image/jpeg")
