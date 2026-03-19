"""JSON-based data persistence layer.

Solves the problem of SQLite database being lost between sessions
(pipeline.db is gitignored and ephemeral environments rebuild from git).

On every write operation, data is exported to git-tracked JSON files in data/.
On startup, if the database is empty, data is restored from these JSON files.
"""

import json
from datetime import datetime
from pathlib import Path
from config.settings import PROJECT_ROOT
from utils.logger import get_logger

logger = get_logger("data_persistence")

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TEMPLATES_FILE = DATA_DIR / "prompt_templates.json"
VEHICLES_FILE = DATA_DIR / "vehicles.json"
BRANDING_FILE = DATA_DIR / "branding.json"


def export_prompt_templates():
    """Export all prompt templates to JSON file."""
    from utils.database import get_all_prompt_templates
    templates = get_all_prompt_templates()
    TEMPLATES_FILE.write_text(json.dumps(templates, indent=2, default=str))
    logger.info("Exported %d prompt templates to %s", len(templates), TEMPLATES_FILE.name)


def export_vehicles():
    """Export all vehicles to JSON file."""
    from utils.database import get_all_vehicles
    vehicles = get_all_vehicles()
    VEHICLES_FILE.write_text(json.dumps(vehicles, indent=2, default=str))
    logger.info("Exported %d vehicles to %s", len(vehicles), VEHICLES_FILE.name)


def export_branding():
    """Export branding settings to JSON file."""
    from utils.database import get_branding_settings
    branding = get_branding_settings()
    if branding:
        BRANDING_FILE.write_text(json.dumps(branding, indent=2, default=str))
        logger.info("Exported branding settings to %s", BRANDING_FILE.name)


def export_all():
    """Export all data to JSON files."""
    export_prompt_templates()
    export_vehicles()
    export_branding()


def restore_prompt_templates():
    """Restore prompt templates from JSON file if DB is empty."""
    if not TEMPLATES_FILE.exists():
        return 0

    from utils.database import get_connection
    conn = get_connection()
    cursor = conn.execute("SELECT COUNT(*) as count FROM prompt_templates")
    existing = cursor.fetchone()["count"]

    if existing > 0:
        conn.close()
        return 0

    templates = json.loads(TEMPLATES_FILE.read_text())
    restored = 0
    for t in templates:
        conn.execute(
            "INSERT INTO prompt_templates (id, display_name, prompt_text, is_default, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (t["id"], t["display_name"], t["prompt_text"], t.get("is_default", 0),
             t.get("created_at", datetime.now().isoformat()),
             t.get("updated_at", datetime.now().isoformat())),
        )
        restored += 1

    conn.commit()
    conn.close()
    logger.info("Restored %d prompt templates from backup", restored)
    return restored


def restore_vehicles():
    """Restore vehicle records from JSON file if DB is empty."""
    if not VEHICLES_FILE.exists():
        return 0

    from utils.database import get_connection
    conn = get_connection()
    cursor = conn.execute("SELECT COUNT(*) as count FROM vehicles")
    existing = cursor.fetchone()["count"]

    if existing > 0:
        conn.close()
        return 0

    vehicles = json.loads(VEHICLES_FILE.read_text())
    restored = 0

    # Get column names from the vehicles table
    cursor = conn.execute("PRAGMA table_info(vehicles)")
    valid_columns = {row["name"] for row in cursor.fetchall()}

    for v in vehicles:
        # Filter to only valid DB columns (exclude joined fields like prompt_template_name)
        db_data = {k: val for k, val in v.items() if k in valid_columns}
        if not db_data.get("cargurus_id"):
            continue

        columns = list(db_data.keys())
        placeholders = ", ".join(["?"] * len(columns))
        values = [db_data[col] for col in columns]

        try:
            conn.execute(
                f"INSERT OR IGNORE INTO vehicles ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
            restored += 1
        except Exception as e:
            logger.warning("Failed to restore vehicle %s: %s", db_data.get("cargurus_id"), e)

    conn.commit()
    conn.close()
    logger.info("Restored %d vehicles from backup", restored)
    return restored


def restore_branding():
    """Restore branding settings from JSON file if DB is empty."""
    if not BRANDING_FILE.exists():
        return False

    from utils.database import get_connection
    conn = get_connection()
    cursor = conn.execute("SELECT COUNT(*) as count FROM branding_settings")
    existing = cursor.fetchone()["count"]

    if existing > 0:
        conn.close()
        return False

    branding = json.loads(BRANDING_FILE.read_text())
    conn.execute(
        "INSERT INTO branding_settings (id, dealer_name, dealer_phone, dealer_address, "
        "dealer_website, dealer_logo_path, updated_at) VALUES (1, ?, ?, ?, ?, ?, ?)",
        (branding.get("dealer_name", ""), branding.get("dealer_phone", ""),
         branding.get("dealer_address", ""), branding.get("dealer_website", ""),
         branding.get("dealer_logo_path", ""), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    logger.info("Restored branding settings from backup")
    return True


def restore_all():
    """Restore all data from JSON backups (only if DB tables are empty)."""
    templates = restore_prompt_templates()
    vehicles = restore_vehicles()
    branding = restore_branding()
    if templates or vehicles or branding:
        logger.info("Session data restored from persistent backup files")
    return templates or vehicles or branding
