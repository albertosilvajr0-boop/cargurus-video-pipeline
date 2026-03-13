"""Generate a video script from VIN-decoded vehicle specs.

No photos needed — builds a rich cinematic prompt from specs alone.
Gemini crafts the Veo prompt to match the exact vehicle description.
"""

import json
import re

from google import genai
from google.genai import types
from rich.console import Console

from config import settings
from utils.retry import retry_sync

console = Console()

VIN_SCRIPT_PROMPT = """You are an expert automotive video scriptwriter creating short-form cinematic content
for a car dealership's social media.

I have ONLY the following decoded VIN specs — no photos, no sticker. Use your knowledge
of this exact vehicle to write the most visually accurate prompt possible.

## Vehicle Specs (from VIN)
- **VIN**: {vin}
- **Year**: {year}
- **Make**: {make}
- **Model**: {model}
- **Trim**: {trim}
- **Body Style**: {body_style}
- **Engine**: {engine}
- **Transmission**: {transmission}
- **Drivetrain**: {drivetrain}
- **Fuel Type**: {fuel_type}
{price_line}

## Dealership
- **Name**: {dealer_name}

## What You Know
You know what a {year} {make} {model} {trim} looks like. Use that knowledge to describe
its exact design language, grille shape, wheel design, headlight style, body lines, and stance.

## Task
Create a JSON response with this structure:

{{
    "vehicle_description": "A detailed visual description of this exact vehicle — what it actually looks like based on your knowledge of this year/make/model. Include color-agnostic details: body shape, grille design, wheel style, headlight shape, stance, proportions.",
    "script": {{
        "hook": "2-3 second attention grabber text",
        "veo_prompt": "An EXTREMELY detailed cinematic prompt for AI video generation. Describe a {year} {make} {model} {trim} in a dramatic setting. Include: exact body style, camera movements (dolly, crane, tracking), lighting (golden hour, studio, dramatic), environment (urban, desert, mountain road), atmosphere (rain, dust, lens flares). The prompt must be detailed enough to generate a convincing 8-second clip WITHOUT any reference photo. Be SPECIFIC about the vehicle's appearance based on your knowledge of this model. 200+ words.",
        "text_overlay": "Short text for video overlay, under 35 chars (e.g. '2024 RAM 1500 Laramie')",
        "cta": "Call-to-action text for the end card",
        "caption": "Social media caption under 150 chars with hashtags",
        "target_emotion": "Primary emotion (excitement, luxury, adventure, power, elegance, freedom)"
    }}
}}

## Guidelines
- The veo_prompt MUST be at least 200 words — this is the most critical field
- Describe the ACTUAL appearance of this specific vehicle (not a generic car)
- Match the emotion to the vehicle segment (truck=power, luxury sedan=elegance, sports car=excitement, SUV=adventure, EV=innovation)
- Don't mention a specific color since we don't know it — describe the vehicle shape and design instead, or say "a stunning" without committing to a color
- Make the veo_prompt cinematic: use film terminology (tracking shot, shallow depth of field, anamorphic, etc.)

Respond ONLY with the JSON object. No markdown code fences.
"""


class VINScriptGenerator:
    """Generates video scripts from VIN-decoded vehicle specs."""

    def __init__(self):
        self.client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        self.model_name = "gemini-2.0-flash"

    def generate(self, vehicle_specs: dict, price: float | int | None = None) -> dict | None:
        """
        Generate a video script from VIN-decoded vehicle specs.

        Args:
            vehicle_specs: Dict from vin_decoder.decode_vin()
            price: Optional price to include in the video

        Returns:
            Parsed script dict, or None on failure
        """
        price_line = f"- **Price**: ${price:,.0f}" if price and price > 0 else ""

        prompt = VIN_SCRIPT_PROMPT.format(
            vin=vehicle_specs.get("vin", ""),
            year=vehicle_specs.get("year", "Unknown"),
            make=vehicle_specs.get("make", "Unknown"),
            model=vehicle_specs.get("model", "Unknown"),
            trim=vehicle_specs.get("trim", ""),
            body_style=vehicle_specs.get("body_style", ""),
            engine=vehicle_specs.get("engine", ""),
            transmission=vehicle_specs.get("transmission", ""),
            drivetrain=vehicle_specs.get("drivetrain", ""),
            fuel_type=vehicle_specs.get("fuel_type", ""),
            price_line=price_line,
            dealer_name=settings.DEALER_NAME,
        )

        console.print("[cyan]Generating video script from VIN specs...[/cyan]")

        response = self._call_gemini(prompt)
        return self._parse_response(response)

    @retry_sync(max_retries=3, base_delay=2.0, operation_name="Gemini VIN script generation")
    def _call_gemini(self, prompt: str):
        return self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.9,
                max_output_tokens=3000,
            ),
        )

    def _parse_response(self, response) -> dict | None:
        text = response.text.strip()
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
            console.print("[red]Failed to parse Gemini response[/red]")
            return None
