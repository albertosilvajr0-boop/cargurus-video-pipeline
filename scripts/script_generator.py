"""AI-powered video script generator for vehicle listing videos.

Uses Google Gemini to create compelling 15-second cinematic video scripts
based on vehicle details, photos, and window sticker highlights.
"""

import json
import re
from datetime import datetime

from google import genai
from google.genai import types
from rich.console import Console

from config import settings
from utils.database import get_vehicles_by_status, update_vehicle_status
from utils.cost_tracker import CostTracker
from utils.retry import retry_sync

console = Console()

SCRIPT_PROMPT_TEMPLATE = """You are an expert automotive video scriptwriter creating short-form cinematic content
for a car dealership's social media. Create a compelling 15-second video script for the following vehicle.

## Vehicle Details
- **Year/Make/Model/Trim**: {year} {make} {model} {trim}
- **Price**: ${price:,.0f}
- **Mileage**: {mileage:,} miles
- **Exterior Color**: {exterior_color}
- **Interior Color**: {interior_color}
- **Engine**: {engine}
- **Transmission**: {transmission}
- **Drivetrain**: {drivetrain}
- **Dealership**: {dealer_name}

## Instructions

Create a JSON response with the following structure:

{{
    "hook": "The opening 2-3 seconds attention grabber (text overlay or dramatic visual)",
    "scenes": [
        {{
            "timestamp": "0:00-0:03",
            "visual_prompt": "Detailed description for AI video generation - camera angle, movement, lighting, mood",
            "text_overlay": "Optional bold text shown on screen",
            "audio_cue": "Sound effect or music mood"
        }},
        {{
            "timestamp": "0:03-0:08",
            "visual_prompt": "...",
            "text_overlay": "...",
            "audio_cue": "..."
        }},
        {{
            "timestamp": "0:08-0:13",
            "visual_prompt": "...",
            "text_overlay": "...",
            "audio_cue": "..."
        }},
        {{
            "timestamp": "0:13-0:15",
            "visual_prompt": "...",
            "text_overlay": "...",
            "audio_cue": "..."
        }}
    ],
    "cta": "Call to action text for the end card",
    "veo_master_prompt": "A single comprehensive prompt to generate the full 8-second cinematic video clip showing this {year} {make} {model}. Include specific camera movements, lighting, atmosphere, and the car's key visual features. Make it dramatic and aspirational. This will be used directly with Google Veo or OpenAI Sora.",
    "veo_extension_prompt": "A prompt for the second 8-second clip that continues from the first, showing different angles or interior details. Maintain cinematic quality.",
    "caption": "A short, punchy social media caption (under 150 chars) with relevant hashtags",
    "target_emotion": "The primary emotion this video should evoke (excitement, luxury, adventure, etc.)"
}}

## Style Guidelines
- Think TikTok/Reels energy — fast cuts, dramatic reveals
- Lead with the most visually striking feature of this vehicle
- Use cinematic language: dolly shots, aerial reveals, golden hour lighting
- The veo_master_prompt should be HIGHLY detailed and cinematic — this is the most important field
- Make the viewer FEEL something — don't just show specs
- End with urgency (limited availability, special price, etc.)
- The video prompts should describe a {exterior_color} {year} {make} {model} specifically

Respond ONLY with the JSON object, no markdown formatting or code blocks.
"""


class ScriptGenerator:
    """Generates video scripts using Google Gemini via the google-genai SDK."""

    def __init__(self):
        self.client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        self.model_name = "gemini-2.0-flash"
        self.cost_tracker = CostTracker()

    async def generate_all_scripts(self):
        """Generate scripts for all vehicles that need them."""
        # Get vehicles that have photos downloaded but no script yet
        vehicles = (
            get_vehicles_by_status("photos_downloaded") +
            get_vehicles_by_status("sticker_downloaded")
        )

        if not vehicles:
            console.print("[yellow]No vehicles pending script generation[/yellow]")
            return

        console.print(f"[cyan]Generating scripts for {len(vehicles)} vehicles...[/cyan]")

        for vehicle in vehicles:
            try:
                script = await self.generate_script(vehicle)
                if script:
                    # Save script to file
                    script_path = settings.SCRIPTS_DIR / f"{vehicle['cargurus_id']}_script.json"
                    script_path.write_text(json.dumps(script, indent=2))

                    update_vehicle_status(
                        vehicle["id"],
                        "script_generated",
                        video_script=json.dumps(script),
                        script_generated_at=datetime.now().isoformat(),
                    )

                    console.print(
                        f"[green]  ✓ {vehicle.get('year', '')} {vehicle.get('make', '')} "
                        f"{vehicle.get('model', '')} — {script.get('target_emotion', 'cinematic')}[/green]"
                    )

            except Exception as e:
                console.print(f"[red]  ✗ Error generating script for {vehicle['id']}: {e}[/red]")
                update_vehicle_status(vehicle["id"], "error", error_message=f"Script generation: {e}")

    @retry_sync(max_retries=3, base_delay=2.0, operation_name="Gemini script generation")
    def _call_gemini(self, prompt: str):
        """Call Gemini API with retry logic."""
        return self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.9,
                max_output_tokens=2000,
            ),
        )

    async def generate_script(self, vehicle: dict) -> dict | None:
        """Generate a video script for a single vehicle."""
        prompt = SCRIPT_PROMPT_TEMPLATE.format(
            year=vehicle.get("year", "Unknown"),
            make=vehicle.get("make", "Unknown"),
            model=vehicle.get("model", "Unknown"),
            trim=vehicle.get("trim", ""),
            price=vehicle.get("price", 0) or 0,
            mileage=vehicle.get("mileage", 0) or 0,
            exterior_color=vehicle.get("exterior_color", "Unknown"),
            interior_color=vehicle.get("interior_color", "Unknown"),
            engine=vehicle.get("engine", "Unknown"),
            transmission=vehicle.get("transmission", "Unknown"),
            drivetrain=vehicle.get("drivetrain", "Unknown"),
            dealer_name=settings.DEALER_NAME,
        )

        response = self._call_gemini(prompt)

        # Parse JSON response
        text = response.text.strip()
        # Clean up common issues - strip markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            script = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON from the response
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                script = json.loads(json_match.group())
            else:
                console.print(f"[red]Failed to parse script JSON[/red]")
                return None

        # Track cost (Gemini Flash is very cheap, ~$0.001 per script)
        self.cost_tracker.record_cost(
            vehicle_id=vehicle["id"],
            engine="gemini",
            quality="flash",
            duration=0,
            cost=0.001,
            call_type="script_generation",
        )

        return script


async def run_script_generator():
    """Convenience function to run the script generator."""
    generator = ScriptGenerator()
    await generator.generate_all_scripts()
