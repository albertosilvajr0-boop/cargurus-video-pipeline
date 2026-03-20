"""SQLite database for tracking vehicles through the pipeline."""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from config.settings import DB_PATH


def get_connection():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database schema."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cargurus_id TEXT UNIQUE,
            vin TEXT,
            year INTEGER,
            make TEXT,
            model TEXT,
            trim TEXT,
            price REAL,
            mileage INTEGER,
            exterior_color TEXT,
            interior_color TEXT,
            engine TEXT,
            transmission TEXT,
            drivetrain TEXT,
            listing_url TEXT,
            
            -- Pipeline status
            status TEXT DEFAULT 'scraped',  -- scraped, photos_downloaded, sticker_downloaded, script_generated, video_generating, video_complete, error
            error_message TEXT,
            
            -- Scraped asset URLs (from scraper)
            photo_urls TEXT DEFAULT '[]',
            sticker_url TEXT,

            -- Downloaded asset paths (from downloader)
            photo_paths TEXT DEFAULT '[]',
            sticker_path TEXT,
            
            -- Generated content
            video_script TEXT,
            video_path TEXT,
            video_url TEXT,  -- GCS public URL (if cloud storage is enabled)
            video_engine TEXT,  -- veo or sora
            video_cost REAL DEFAULT 0.0,
            prompt_template_id INTEGER,  -- which prompt template was used
            
            -- Timestamps
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            script_generated_at TIMESTAMP,
            video_generated_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            vehicles_scraped INTEGER DEFAULT 0,
            vehicles_processed INTEGER DEFAULT 0,
            videos_generated INTEGER DEFAULT 0,
            total_cost REAL DEFAULT 0.0,
            status TEXT DEFAULT 'running',  -- running, completed, error
            error_message TEXT
        );
        
        CREATE TABLE IF NOT EXISTS cost_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER,
            engine TEXT,
            quality TEXT,
            duration_seconds REAL,
            cost REAL,
            api_call_type TEXT,  -- script_generation, video_generation
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
        );

        CREATE TABLE IF NOT EXISTS prompt_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            display_name TEXT NOT NULL,
            prompt_text TEXT NOT NULL,
            is_default INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS branding_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            dealer_name TEXT NOT NULL DEFAULT '',
            dealer_phone TEXT NOT NULL DEFAULT '',
            dealer_address TEXT NOT NULL DEFAULT '',
            dealer_website TEXT NOT NULL DEFAULT '',
            dealer_logo_path TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS media_library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL DEFAULT '',
            file_path TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_type TEXT NOT NULL DEFAULT 'photo',  -- photo, sticker, carfax
            media_group TEXT NOT NULL DEFAULT '',      -- groups related files together
            thumbnail_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Migrate: add prompt_template_id column if missing (existing databases)
    try:
        conn.execute("SELECT prompt_template_id FROM vehicles LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE vehicles ADD COLUMN prompt_template_id INTEGER")
    # Migrate: add video_url column if missing (existing databases)
    try:
        conn.execute("SELECT video_url FROM vehicles LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE vehicles ADD COLUMN video_url TEXT")
    conn.commit()
    conn.close()


def upsert_vehicle(vehicle_data: dict) -> int:
    """Insert or update a vehicle record. Returns the vehicle ID."""
    conn = get_connection()
    cursor = conn.cursor()

    # Check if vehicle exists
    cursor.execute("SELECT id FROM vehicles WHERE cargurus_id = ?", (vehicle_data.get("cargurus_id"),))
    row = cursor.fetchone()

    if row:
        # Update existing
        vehicle_id = row["id"]
        fields = []
        values = []
        for key, value in vehicle_data.items():
            if key != "cargurus_id":
                fields.append(f"{key} = ?")
                values.append(value)
        fields.append("updated_at = ?")
        values.append(datetime.now().isoformat())
        values.append(vehicle_id)

        cursor.execute(f"UPDATE vehicles SET {', '.join(fields)} WHERE id = ?", values)
    else:
        # Insert new
        columns = list(vehicle_data.keys())
        placeholders = ", ".join(["?"] * len(columns))
        values = [vehicle_data[col] for col in columns]

        cursor.execute(
            f"INSERT INTO vehicles ({', '.join(columns)}) VALUES ({placeholders})",
            values
        )
        vehicle_id = cursor.lastrowid

    conn.commit()
    conn.close()
    _auto_export_vehicles()
    return vehicle_id


def update_vehicle_status(vehicle_id: int, status: str, **kwargs):
    """Update a vehicle's pipeline status and optional fields."""
    conn = get_connection()
    fields = ["status = ?", "updated_at = ?"]
    values = [status, datetime.now().isoformat()]

    for key, value in kwargs.items():
        fields.append(f"{key} = ?")
        values.append(value)

    values.append(vehicle_id)
    conn.execute(f"UPDATE vehicles SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    conn.close()
    _auto_export_vehicles()


def get_vehicles_by_status(status: str) -> list:
    """Get all vehicles with a given status."""
    conn = get_connection()
    cursor = conn.execute("SELECT * FROM vehicles WHERE status = ? ORDER BY id", (status,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_all_vehicles() -> list:
    """Get all vehicles with prompt template name."""
    conn = get_connection()
    cursor = conn.execute("""
        SELECT v.*, pt.display_name AS prompt_template_name
        FROM vehicles v
        LEFT JOIN prompt_templates pt ON v.prompt_template_id = pt.id
        ORDER BY v.id
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_pipeline_stats() -> dict:
    """Get summary statistics of the pipeline."""
    conn = get_connection()
    
    stats = {}
    cursor = conn.execute("SELECT status, COUNT(*) as count FROM vehicles GROUP BY status")
    stats["by_status"] = {row["status"]: row["count"] for row in cursor.fetchall()}
    
    cursor = conn.execute("SELECT COUNT(*) as total FROM vehicles")
    stats["total_vehicles"] = cursor.fetchone()["total"]
    
    cursor = conn.execute("SELECT COALESCE(SUM(video_cost), 0) as total_cost FROM vehicles")
    vehicle_cost = cursor.fetchone()["total_cost"]
    cursor = conn.execute("SELECT COALESCE(SUM(cost), 0) as total FROM cost_log")
    log_cost_total = cursor.fetchone()["total"]
    stats["total_cost"] = max(vehicle_cost, log_cost_total)
    
    cursor = conn.execute("SELECT COUNT(*) as count FROM vehicles WHERE video_path IS NOT NULL")
    stats["videos_completed"] = cursor.fetchone()["count"]
    
    conn.close()
    return stats


def log_cost(vehicle_id: int, engine: str, quality: str, duration: float, cost: float, call_type: str):
    """Log an API cost entry."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO cost_log (vehicle_id, engine, quality, duration_seconds, cost, api_call_type) VALUES (?, ?, ?, ?, ?, ?)",
        (vehicle_id, engine, quality, duration, cost, call_type)
    )
    conn.commit()
    conn.close()


def get_total_spend() -> float:
    """Get total spend across all cost log entries."""
    conn = get_connection()
    cursor = conn.execute("SELECT COALESCE(SUM(cost), 0) as total FROM cost_log")
    total = cursor.fetchone()["total"]
    conn.close()
    return total


def get_cost_analytics() -> dict:
    """Get detailed cost analytics for the Costs dashboard tab."""
    conn = get_connection()
    analytics = {}

    # Total spend (from cost_log for accuracy)
    cursor = conn.execute("SELECT COALESCE(SUM(cost), 0) as total FROM cost_log")
    analytics["total_spend"] = cursor.fetchone()["total"]

    # Total from vehicles table (fallback / cross-check)
    cursor = conn.execute("SELECT COALESCE(SUM(video_cost), 0) as total FROM vehicles")
    analytics["total_vehicle_cost"] = cursor.fetchone()["total"]

    # Use the higher of the two as the canonical total
    analytics["total_cost"] = max(analytics["total_spend"], analytics["total_vehicle_cost"])

    # Video count
    cursor = conn.execute("SELECT COUNT(*) as count FROM vehicles WHERE video_path IS NOT NULL")
    analytics["videos_completed"] = cursor.fetchone()["count"]

    # Average cost per video
    if analytics["videos_completed"] > 0:
        analytics["avg_cost_per_video"] = analytics["total_cost"] / analytics["videos_completed"]
    else:
        analytics["avg_cost_per_video"] = 0.0

    # Spend by engine
    cursor = conn.execute(
        "SELECT engine, COUNT(*) as count, SUM(cost) as total "
        "FROM cost_log WHERE api_call_type = 'video_generation' GROUP BY engine"
    )
    analytics["by_engine"] = [dict(row) for row in cursor.fetchall()]

    # Spend by call type
    cursor = conn.execute(
        "SELECT api_call_type, COUNT(*) as count, SUM(cost) as total "
        "FROM cost_log GROUP BY api_call_type"
    )
    analytics["by_type"] = [dict(row) for row in cursor.fetchall()]

    # Spend by day (last 30 days)
    cursor = conn.execute(
        "SELECT DATE(created_at) as day, COUNT(*) as count, SUM(cost) as total "
        "FROM cost_log GROUP BY DATE(created_at) ORDER BY day DESC LIMIT 30"
    )
    analytics["by_day"] = [dict(row) for row in cursor.fetchall()]

    # Recent cost entries (last 20)
    cursor = conn.execute(
        "SELECT cl.*, v.year, v.make, v.model "
        "FROM cost_log cl LEFT JOIN vehicles v ON cl.vehicle_id = v.id "
        "ORDER BY cl.created_at DESC LIMIT 20"
    )
    analytics["recent"] = [dict(row) for row in cursor.fetchall()]

    # Budget info
    from config.settings import COST_LIMIT
    analytics["budget_limit"] = COST_LIMIT
    analytics["budget_remaining"] = max(0, COST_LIMIT - analytics["total_cost"])
    analytics["budget_used_pct"] = min(100, (analytics["total_cost"] / COST_LIMIT * 100)) if COST_LIMIT > 0 else 0

    conn.close()
    return analytics


def retry_failed_vehicles(target_status: str = "scraped") -> int:
    """Reset all vehicles with 'error' status back to a retryable state.

    Args:
        target_status: The status to reset vehicles to (default: 'scraped')

    Returns:
        Number of vehicles reset.
    """
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE vehicles SET status = ?, error_message = NULL, updated_at = ? "
        "WHERE status = 'error'",
        (target_status, datetime.now().isoformat()),
    )
    count = cursor.rowcount
    conn.commit()
    conn.close()
    if count > 0:
        _auto_export_vehicles()
    return count


### Prompt Templates CRUD ###

SHOWROOM_VIDEO_PROMPT = """You are generating a standardized, high-fidelity Sora/Veo prompt for a professional vehicle walkaround.
You must integrate a professional presenter and place the vehicle in a controlled, ultra-premium showroom.
Precision of text and consistency of the environment are the highest priorities.

## Data Extraction Protocol
From the vehicle info provided, extract:
- Vehicle Identity: [Year] [Make] [Model] [Trim]
- Visual Specs: Paint name, interior color, wheel type
- Trust Data: Carfax status (e.g., "1-Owner") and MSRP
- Key Features: Select 2 premium features for the walkaround

## The "Zero Variation" Production Manifest
Generate the veo_prompt using this structure:

[VEHICLE NAME] - Professional Walkaround

Subject: A pristine [EXTERIOR COLOR] [YEAR] [MAKE] [MODEL]. The presenter is a professional salesperson wearing a navy blazer and grey chinos.

Environment: A minimalist, high-end automotive studio. The floor is dark obsidian-polished tile. The background is a solid, neutral-toned architectural wall — no windows, no cityscape. Lighting is provided by overhead linear "ribbon" soft-boxes that create perfectly straight, crisp reflections on the car's paint. The vehicle glass is 100% clean with no stickers or decals.

Motion Sequence:
0-15s: Wide hero shot of the presenter and the car in the dark studio.
15-45s: Close-up pans of [FEATURE 1] and [FEATURE 2]. The presenter gestures toward them with calm, professional movements.
45-60s: Camera pulls back to center the presenter.

Text & Contact Integration:
During the final 10 seconds, a digitally clear, bold white graphic appears at the bottom center of the frame.
The text MUST read exactly: "Call {dealer_phone}".
The characters must be static, legible, and maintain a consistent sans-serif font. No flickering or morphing of the numbers.

Cinematography: 8K, Arri Alexa, 35mm f/2.8 lens. High contrast between the car and the dark background. Focus is locked on the presenter and the vehicle."""


def seed_default_templates():
    """Insert the default Showroom Video template if no templates exist."""
    conn = get_connection()
    cursor = conn.execute("SELECT COUNT(*) as count FROM prompt_templates")
    if cursor.fetchone()["count"] == 0:
        conn.execute(
            "INSERT INTO prompt_templates (display_name, prompt_text, is_default) VALUES (?, ?, 1)",
            ("Showroom Video", SHOWROOM_VIDEO_PROMPT),
        )
        conn.commit()
        _auto_export_templates()
    conn.close()


def get_all_prompt_templates() -> list:
    """Get all prompt templates."""
    conn = get_connection()
    cursor = conn.execute("SELECT * FROM prompt_templates ORDER BY is_default DESC, display_name")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_prompt_template(template_id: int) -> dict | None:
    """Get a single prompt template by ID."""
    conn = get_connection()
    cursor = conn.execute("SELECT * FROM prompt_templates WHERE id = ?", (template_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def create_prompt_template(display_name: str, prompt_text: str) -> int:
    """Create a new prompt template. Returns the new template ID."""
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO prompt_templates (display_name, prompt_text) VALUES (?, ?)",
        (display_name, prompt_text),
    )
    template_id = cursor.lastrowid
    conn.commit()
    conn.close()
    _auto_export_templates()
    return template_id


def update_prompt_template(template_id: int, display_name: str, prompt_text: str) -> bool:
    """Update an existing prompt template. Returns True if found and updated."""
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE prompt_templates SET display_name = ?, prompt_text = ?, updated_at = ? WHERE id = ?",
        (display_name, prompt_text, datetime.now().isoformat(), template_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if updated:
        _auto_export_templates()
    return updated


def delete_prompt_template(template_id: int) -> bool:
    """Delete a prompt template. Returns True if found and deleted."""
    conn = get_connection()
    cursor = conn.execute("DELETE FROM prompt_templates WHERE id = ?", (template_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if deleted:
        _auto_export_templates()
    return deleted


### Media Library CRUD ###

def save_media_item(label: str, file_path: str, file_name: str,
                    file_type: str = "photo", media_group: str = "") -> int:
    """Save a media item to the library. Returns the new media item ID."""
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO media_library (label, file_path, file_name, file_type, media_group) "
        "VALUES (?, ?, ?, ?, ?)",
        (label, file_path, file_name, file_type, media_group),
    )
    item_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return item_id


def get_all_media(media_group: str | None = None) -> list:
    """Get all media items, optionally filtered by group."""
    conn = get_connection()
    if media_group:
        cursor = conn.execute(
            "SELECT * FROM media_library WHERE media_group = ? ORDER BY created_at DESC",
            (media_group,),
        )
    else:
        cursor = conn.execute("SELECT * FROM media_library ORDER BY created_at DESC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_media_groups() -> list:
    """Get distinct media groups with counts."""
    conn = get_connection()
    cursor = conn.execute(
        "SELECT media_group, COUNT(*) as count, MIN(created_at) as first_added, "
        "MAX(created_at) as last_added "
        "FROM media_library WHERE media_group != '' "
        "GROUP BY media_group ORDER BY MAX(created_at) DESC"
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_media_items_by_ids(ids: list[int]) -> list:
    """Get media items by a list of IDs."""
    if not ids:
        return []
    conn = get_connection()
    placeholders = ",".join(["?"] * len(ids))
    cursor = conn.execute(
        f"SELECT * FROM media_library WHERE id IN ({placeholders}) ORDER BY file_type, id",
        ids,
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def delete_media_item(item_id: int) -> bool:
    """Delete a media item. Returns True if found and deleted."""
    conn = get_connection()
    cursor = conn.execute("DELETE FROM media_library WHERE id = ?", (item_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def delete_media_group(group_name: str) -> int:
    """Delete all media items in a group. Returns number deleted."""
    conn = get_connection()
    cursor = conn.execute("DELETE FROM media_library WHERE media_group = ?", (group_name,))
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count


def update_media_group_label(group_name: str, new_label: str) -> bool:
    """Update the label for all items in a media group."""
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE media_library SET label = ? WHERE media_group = ?",
        (new_label, group_name),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


### Branding Settings (persistent across deployments) ###

def save_branding_settings(dealer_name: str, dealer_phone: str, dealer_address: str,
                           dealer_website: str, dealer_logo_path: str):
    """Save branding settings to the database (upsert single row)."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO branding_settings (id, dealer_name, dealer_phone, dealer_address,
           dealer_website, dealer_logo_path, updated_at)
           VALUES (1, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             dealer_name=excluded.dealer_name,
             dealer_phone=excluded.dealer_phone,
             dealer_address=excluded.dealer_address,
             dealer_website=excluded.dealer_website,
             dealer_logo_path=excluded.dealer_logo_path,
             updated_at=excluded.updated_at""",
        (dealer_name, dealer_phone, dealer_address, dealer_website,
         dealer_logo_path, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    _auto_export_branding()


def get_branding_settings() -> dict | None:
    """Load branding settings from the database. Returns None if not yet saved."""
    conn = get_connection()
    cursor = conn.execute("SELECT * FROM branding_settings WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def retry_vehicle_by_id(vehicle_id: int, target_status: str = "scraped") -> bool:
    """Reset a single vehicle from error status back to a retryable state.

    Returns:
        True if the vehicle was reset, False if not found or not in error state.
    """
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE vehicles SET status = ?, error_message = NULL, updated_at = ? "
        "WHERE id = ? AND status = 'error'",
        (target_status, datetime.now().isoformat(), vehicle_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if updated:
        _auto_export_vehicles()
    return updated


# --- Auto-export helpers (persist DB data to git-tracked JSON files) ---

def _auto_export_templates():
    """Export prompt templates to JSON backup after any write."""
    try:
        from utils.data_persistence import export_prompt_templates
        export_prompt_templates()
    except Exception:
        pass  # Don't let export failures break the main operation


def _auto_export_vehicles():
    """Export vehicles to JSON backup after any write."""
    try:
        from utils.data_persistence import export_vehicles
        export_vehicles()
    except Exception:
        pass


def _auto_export_branding():
    """Export branding to JSON backup after any write."""
    try:
        from utils.data_persistence import export_branding
        export_branding()
    except Exception:
        pass
