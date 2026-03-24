"""VIN decoder using the free NHTSA vPIC API.

Decodes a 17-character VIN into year, make, model, trim, engine,
drivetrain, body style, and other vehicle details — completely free,
no API key required.

API docs: https://vpic.nhtsa.dot.gov/api/
"""

import logging

import httpx
from rich.console import Console

from utils.retry import retry_sync

console = Console()
logger = logging.getLogger("vin_decoder")

NHTSA_DECODE_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"


# Map NHTSA field names to our internal field names
FIELD_MAP = {
    "ModelYear": "year",
    "Make": "make",
    "Model": "model",
    "Trim": "trim",
    "BodyClass": "body_style",
    "DriveType": "drivetrain",
    "EngineCylinders": "engine_cylinders",
    "EngineModel": "engine_model",
    "DisplacementL": "displacement_liters",
    "EngineHP": "engine_hp",
    "FuelTypePrimary": "fuel_type",
    "TransmissionStyle": "transmission",
    "TransmissionSpeeds": "transmission_speeds",
    "Doors": "doors",
    "PlantCity": "plant_city",
    "PlantCountry": "plant_country",
    "VehicleType": "vehicle_type",
    "GVWR": "gvwr",
    "Series": "series",
    "Series2": "series2",
    "ExteriorColor": "exterior_color",  # rarely populated but sometimes present
}


def validate_vin(vin: str) -> str | None:
    """Validate and normalize a VIN. Returns cleaned VIN or None if invalid."""
    vin = vin.strip().upper().replace(" ", "").replace("-", "")
    if len(vin) != 17:
        return None
    # VINs don't contain I, O, or Q
    if any(c in vin for c in "IOQ"):
        return None
    if not vin.isalnum():
        return None
    return vin


@retry_sync(max_retries=3, base_delay=1.0, operation_name="NHTSA VIN decode")
def _fetch_nhtsa(vin: str) -> dict:
    """Call the NHTSA API."""
    url = NHTSA_DECODE_URL.format(vin=vin)
    with httpx.Client(timeout=15.0) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def decode_vin(vin: str) -> dict | None:
    """
    Decode a VIN using the NHTSA vPIC API.

    Returns a dict with vehicle details, or None on failure.
    """
    clean_vin = validate_vin(vin)
    if not clean_vin:
        console.print(f"[red]Invalid VIN: {vin}[/red]")
        return None

    console.print(f"[cyan]Decoding VIN: {clean_vin}...[/cyan]")

    try:
        data = _fetch_nhtsa(clean_vin)
    except Exception as e:
        console.print(f"[red]NHTSA API error: {e}[/red]")
        return None

    results = data.get("Results", [])
    if not results:
        console.print("[red]No results from NHTSA[/red]")
        return None

    raw = results[0]

    # Check for decode errors
    error_code = raw.get("ErrorCode", "0")
    if error_code and error_code != "0":
        error_text = raw.get("ErrorText", "Unknown error")
        # Error code 0 = no error, other codes may still return partial data
        if "1" not in error_code.split(","):
            console.print(f"[yellow]NHTSA warning: {error_text}[/yellow]")

    # Map NHTSA fields to our format
    vehicle = {"vin": clean_vin}
    for nhtsa_key, our_key in FIELD_MAP.items():
        value = raw.get(nhtsa_key, "")
        if value and value.strip() and value.strip().lower() != "not applicable":
            vehicle[our_key] = value.strip()

    # Build a human-readable engine string
    engine_parts = []
    if vehicle.get("displacement_liters"):
        engine_parts.append(f"{float(vehicle['displacement_liters']):.1f}L")
    if vehicle.get("engine_cylinders"):
        cyl = vehicle["engine_cylinders"]
        engine_parts.append(f"V{cyl}" if int(cyl) >= 6 else f"{cyl}-cyl")
    if vehicle.get("engine_hp"):
        engine_parts.append(f"{vehicle['engine_hp']}hp")
    if vehicle.get("fuel_type") and vehicle["fuel_type"].lower() != "gasoline":
        engine_parts.append(vehicle["fuel_type"])
    vehicle["engine"] = " ".join(engine_parts) if engine_parts else ""

    # Build transmission string
    trans_parts = []
    if vehicle.get("transmission_speeds"):
        trans_parts.append(f"{vehicle['transmission_speeds']}-Speed")
    if vehicle.get("transmission"):
        trans_parts.append(vehicle["transmission"])
    vehicle["transmission"] = " ".join(trans_parts) if trans_parts else ""

    # Parse year as int
    if vehicle.get("year"):
        try:
            vehicle["year"] = int(vehicle["year"])
        except ValueError:
            logger.warning("Could not parse year as int: %s", vehicle["year"])

    # Build vehicle name
    parts = [str(vehicle.get("year", "")), vehicle.get("make", ""), vehicle.get("model", "")]
    if vehicle.get("trim"):
        parts.append(vehicle["trim"])
    vehicle["vehicle_name"] = " ".join(p for p in parts if p).strip()

    console.print(f"[green]Decoded: {vehicle['vehicle_name']}[/green]")
    if vehicle.get("engine"):
        console.print(f"[green]  Engine: {vehicle['engine']}[/green]")
    if vehicle.get("drivetrain"):
        console.print(f"[green]  Drivetrain: {vehicle['drivetrain']}[/green]")
    if vehicle.get("body_style"):
        console.print(f"[green]  Body: {vehicle['body_style']}[/green]")

    return vehicle
