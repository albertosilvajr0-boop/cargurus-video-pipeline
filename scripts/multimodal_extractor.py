"""Multimodal vehicle extraction and script generation.

Sends uploaded photos, window sticker, and Carfax to Gemini 2.0 Flash
in a single call. Gemini OCRs the documents, analyzes the photos, extracts
all vehicle details, and generates a video script — replacing the old
scrape → download → script pipeline.
"""

import json
import re
import traceback
from pathlib import Path

from google import genai
from google.genai import types
from rich.console import Console

from config import settings
from utils.logger import get_logger
from utils.retry import retry_sync

console = Console()
logger = get_logger("extractor")

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

## Task 2: Feature Selection Rules

From the window sticker, Carfax, and photos, you MUST pick exactly:
- **1 safety feature** (e.g., Blind Spot Monitoring, Forward Collision Warning, Adaptive Cruise Control, Lane Keep Assist, 360-degree Camera, Automatic Emergency Braking)
- **1 technology feature** (e.g., Wireless Apple CarPlay/Android Auto, Head-Up Display, Digital Instrument Cluster, Premium Audio System, Wireless Charging Pad, Panoramic Sunroof with Ambient Lighting)

These must be REAL features that actually exist on the vehicle — pull them from the window sticker options list if available, or identify them from the photos (e.g., a visible 360-camera, heads-up display reflection, branded speaker grille). Do NOT invent features.

## Task 3: 1-Owner & Clean Carfax Rule

If the Carfax report shows:
- **1 previous owner** (or "1-Owner"), AND/OR
- **Clean title / no accidents reported** ("Clean Carfax")

Then you MUST include this in the script. Specifically:
- Add "1-Owner" and/or "Clean Carfax" to the `carfax_highlights` field
- The `hook` or `text_overlay` MUST mention "1-Owner Clean Carfax" (if both apply) or whichever applies
- This is a major selling point — never omit it when the data supports it

## Task 4: Generate Video Script

Create a compelling 15-second video script for {dealer_name}'s social media.
The final video will be: [2s branded intro] + [8s AI-generated cinematic clip] + [5s CTA outro].

You are writing the prompt for the 8-second AI video generation clip.

The script should naturally weave in the selected safety feature and tech feature.

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
        "service_highlights": "N/A - new vehicle",
        "is_one_owner": false,
        "is_clean_carfax": true,
        "carfax_highlights": "Clean Carfax"
    }},
    "selected_features": {{
        "safety_feature": "Forward Collision Warning with Active Braking",
        "tech_feature": "Wireless Apple CarPlay & Android Auto"
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
- ALWAYS include exactly 1 safety feature and 1 tech feature in selected_features — these should appear naturally in the veo_prompt scene
- If the Carfax shows 1-Owner and/or Clean Carfax, the text_overlay or hook MUST mention it (e.g. "1-Owner Clean Carfax | $52,990")
- The carfax_highlights field should be a short string like "1-Owner Clean Carfax" or "Clean Carfax" or null if no carfax provided

Respond ONLY with the JSON object. No markdown code fences.
"""


class MultimodalExtractor:
    """Extracts vehicle data and generates scripts from uploaded images."""

    def __init__(self):
        self.client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        self.model_name = "gemini-2.5-flash"
        self._last_error: str | None = None
        logger.info("MultimodalExtractor initialized (model=%s)", self.model_name)

    def extract_and_script(self, image_paths: list[str], prompt_template: dict | None = None) -> dict | None:
        """
        Send all uploaded images to Gemini in one call.

        Args:
            image_paths: List of file paths (photos, sticker, carfax)
            prompt_template: Optional prompt template dict with 'prompt_text' to override default style

        Returns:
            Parsed JSON dict with vehicle details and script, or None on failure
        """
        if not image_paths:
            logger.warning("extract_and_script called with no image paths")
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
            logger.warning("No valid image files found from paths: %s", image_paths)
            console.print("[red]No valid image files found[/red]")
            return None

        # Add the text prompt
        prompt_text = EXTRACTION_PROMPT.format(dealer_name=settings.DEALER_NAME)

        # Append video style template if provided
        if prompt_template and prompt_template.get("prompt_text"):
            template_text = prompt_template["prompt_text"].replace("{dealer_phone}", settings.DEALER_PHONE or "")
            prompt_text += (
                "\n\n## Video Style Template\n"
                "IMPORTANT: Override the default veo_prompt style with the following production template. "
                "Adapt this template to the specific vehicle while keeping the structure and environment exactly as described:\n\n"
                + template_text
            )

        parts.append(types.Part.from_text(text=prompt_text))

        num_images = len(parts) - 1  # exclude the text prompt part added below
        logger.info(
            "Sending %d images to Gemini for extraction (template=%s)",
            len(parts), bool(prompt_template),
        )
        console.print(f"[cyan]Sending {num_images} images to Gemini for analysis...[/cyan]")

        try:
            response = self._call_gemini(parts)
        except Exception as e:
            logger.error("Gemini API call failed: %s: %s", type(e).__name__, e)
            logger.debug("Gemini traceback:\n%s", traceback.format_exc())
            console.print(f"[red]Gemini API error: {type(e).__name__}: {e}[/red]")
            self._last_error = f"Gemini API error: {type(e).__name__}: {e}"
            return None

        if response is None:
            logger.error("Gemini returned None response")
            console.print("[red]Gemini returned empty response[/red]")
            self._last_error = "Gemini returned empty response"
            return None

        logger.debug("Gemini response received — parsing JSON...")
        parsed = self._parse_response(response)
        if parsed is None:
            self._last_error = getattr(self, "_last_error", "Failed to parse Gemini response")
            logger.error("Gemini response parse failed: %s", self._last_error)
        else:
            vehicle = parsed.get("vehicle", {})
            logger.info(
                "Gemini extraction successful — vehicle: %s %s %s",
                vehicle.get("year", "?"), vehicle.get("make", "?"), vehicle.get("model", "?"),
            )
        return parsed

    @retry_sync(max_retries=3, base_delay=2.0, operation_name="Gemini multimodal extraction")
    def _call_gemini(self, parts: list):
        """Call Gemini with multimodal content."""
        return self.client.models.generate_content(
            model=self.model_name,
            contents=types.Content(parts=parts),
            config=types.GenerateContentConfig(
                temperature=0.7,
                max_output_tokens=8192,
            ),
        )

    def _parse_response(self, response) -> dict | None:
        """Parse JSON from Gemini response."""
        try:
            text = response.text
            if not text:
                console.print(f"[red]Gemini response text is empty. Candidates: {response.candidates}[/red]")
                self._last_error = "Gemini returned empty text — possible safety block or thinking-only response"
                return None
            text = text.strip()
        except Exception as e:
            console.print(f"[red]Could not read Gemini response text: {e}[/red]")
            self._last_error = f"Could not read response: {e}"
            return None

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
            console.print(f"[red]Failed to parse Gemini extraction response. First 500 chars: {text[:500]}[/red]")
            self._last_error = f"JSON parse failed. Response starts with: {text[:200]}"
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
