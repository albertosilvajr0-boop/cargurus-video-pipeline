"""Persistent data layer using Firestore (primary) and local JSON (fallback).

Solves the problem of SQLite database being lost on Cloud Run container restarts.
Firestore provides durable, managed storage that survives any restart or redeployment.

On every write operation, data is exported to Firestore AND local JSON files.
On startup, if the database is empty, data is restored from Firestore (or JSON fallback).
"""

import json
import threading
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
MEDIA_LIBRARY_FILE = DATA_DIR / "media_library.json"
PEOPLE_FILE = DATA_DIR / "people.json"

# Firestore client (lazy-initialized)
_firestore_client = None
_firestore_lock = threading.Lock()
_firestore_available = None  # None = not checked, True/False = cached result

# Firestore collection names
FS_COLLECTION = "app_data"
FS_TEMPLATES_DOC = "prompt_templates"
FS_VEHICLES_DOC = "vehicles"
FS_BRANDING_DOC = "branding"
FS_MEDIA_LIBRARY_DOC = "media_library"
FS_PEOPLE_DOC = "people"


def _get_firestore():
    """Get or create a Firestore client. Returns None if unavailable."""
    global _firestore_client, _firestore_available
    if _firestore_available is False:
        return None

    with _firestore_lock:
        if _firestore_client is not None:
            return _firestore_client
        if _firestore_available is False:
            return None
        try:
            from google.cloud import firestore
            _firestore_client = firestore.Client()
            _firestore_available = True
            logger.info("Firestore client initialized successfully")
            return _firestore_client
        except Exception as e:
            _firestore_available = False
            logger.warning("Firestore unavailable, using local JSON only: %s", e)
            return None


def _save_to_firestore(doc_name: str, data):
    """Save data to a Firestore document. Silently fails if Firestore is unavailable."""
    client = _get_firestore()
    if not client:
        return False
    try:
        # Firestore documents have a 1MB limit, so for large data we chunk it
        # For our use case (templates, vehicles, branding), data fits in one doc
        doc_ref = client.collection(FS_COLLECTION).document(doc_name)
        doc_ref.set({
            "data": json.dumps(data, default=str),
            "updated_at": datetime.now().isoformat(),
        })
        logger.info("Saved %s to Firestore", doc_name)
        return True
    except Exception as e:
        logger.warning("Failed to save %s to Firestore: %s", doc_name, e)
        return False


def _load_from_firestore(doc_name: str):
    """Load data from a Firestore document. Returns None if unavailable."""
    client = _get_firestore()
    if not client:
        return None
    try:
        doc_ref = client.collection(FS_COLLECTION).document(doc_name)
        doc = doc_ref.get()
        if doc.exists:
            raw = doc.to_dict().get("data")
            if raw:
                data = json.loads(raw)
                logger.info("Loaded %s from Firestore (%d items)", doc_name,
                            len(data) if isinstance(data, list) else 1)
                return data
        return None
    except Exception as e:
        logger.warning("Failed to load %s from Firestore: %s", doc_name, e)
        return None


# --- Export functions (save to Firestore + local JSON) ---

def export_prompt_templates():
    """Export all prompt templates to Firestore and local JSON file."""
    from utils.database import get_all_prompt_templates
    templates = get_all_prompt_templates()
    # Save to Firestore (primary)
    saved = _save_to_firestore(FS_TEMPLATES_DOC, templates)
    if not saved:
        logger.warning("Templates NOT saved to Firestore — will be lost on restart!")
    # Save to local JSON (fallback — only survives within same container)
    TEMPLATES_FILE.write_text(json.dumps(templates, indent=2, default=str))
    logger.info("Exported %d prompt templates (firestore=%s)", len(templates), saved)


def export_vehicles():
    """Export all vehicles to Firestore and local JSON file."""
    from utils.database import get_all_vehicles
    vehicles = get_all_vehicles()
    # Save to Firestore (primary)
    saved = _save_to_firestore(FS_VEHICLES_DOC, vehicles)
    if not saved:
        logger.warning("Vehicles NOT saved to Firestore — will be lost on restart!")
    # Save to local JSON (fallback — only survives within same container)
    VEHICLES_FILE.write_text(json.dumps(vehicles, indent=2, default=str))
    logger.info("Exported %d vehicles (firestore=%s)", len(vehicles), saved)


def export_branding():
    """Export branding settings to Firestore and local JSON file."""
    from utils.database import get_branding_settings
    branding = get_branding_settings()
    if branding:
        # Save to Firestore (primary)
        saved = _save_to_firestore(FS_BRANDING_DOC, branding)
        if not saved:
            logger.warning("Branding NOT saved to Firestore — will be lost on restart!")
        # Save to local JSON (fallback — only survives within same container)
        BRANDING_FILE.write_text(json.dumps(branding, indent=2, default=str))
        logger.info("Exported branding settings (firestore=%s)", saved)


def export_media_library():
    """Export all media library records to Firestore and local JSON file."""
    from utils.database import get_all_media
    items = get_all_media()
    saved = _save_to_firestore(FS_MEDIA_LIBRARY_DOC, items)
    if not saved:
        logger.warning("Media library NOT saved to Firestore — labels will be lost on restart!")
    MEDIA_LIBRARY_FILE.write_text(json.dumps(items, indent=2, default=str))
    logger.info("Exported %d media library records (firestore=%s)", len(items), saved)


def export_people():
    """Export all people and their photos to Firestore and local JSON file."""
    from utils.database import get_all_people
    people = get_all_people()
    saved = _save_to_firestore(FS_PEOPLE_DOC, people)
    if not saved:
        logger.warning("People NOT saved to Firestore — will be lost on restart!")
    PEOPLE_FILE.write_text(json.dumps(people, indent=2, default=str))
    logger.info("Exported %d people (firestore=%s)", len(people), saved)


def export_all():
    """Export all data to Firestore and JSON files."""
    export_prompt_templates()
    export_vehicles()
    export_branding()
    export_media_library()
    export_people()


# --- Restore functions (load from Firestore first, then JSON fallback) ---

def restore_prompt_templates():
    """Restore prompt templates from Firestore (or JSON fallback) if DB is empty."""
    from utils.database import get_connection
    conn = get_connection()
    cursor = conn.execute("SELECT COUNT(*) as count FROM prompt_templates")
    existing = cursor.fetchone()["count"]

    if existing > 0:
        conn.close()
        return 0

    # Try Firestore first
    templates = _load_from_firestore(FS_TEMPLATES_DOC)

    # Fall back to local JSON
    if not templates and TEMPLATES_FILE.exists():
        try:
            templates = json.loads(TEMPLATES_FILE.read_text())
            logger.info("Loaded templates from local JSON fallback")
        except Exception:
            templates = None

    if not templates:
        conn.close()
        return 0

    restored = 0
    for t in templates:
        try:
            conn.execute(
                "INSERT INTO prompt_templates (id, display_name, prompt_text, is_default, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (t["id"], t["display_name"], t["prompt_text"], t.get("is_default", 0),
                 t.get("created_at", datetime.now().isoformat()),
                 t.get("updated_at", datetime.now().isoformat())),
            )
            restored += 1
        except Exception as e:
            logger.warning("Failed to restore template %s: %s", t.get("display_name"), e)

    conn.commit()
    conn.close()
    logger.info("Restored %d prompt templates from backup", restored)
    return restored


def restore_vehicles():
    """Restore vehicle records from Firestore (or JSON fallback) if DB is empty."""
    from utils.database import get_connection
    conn = get_connection()
    cursor = conn.execute("SELECT COUNT(*) as count FROM vehicles")
    existing = cursor.fetchone()["count"]

    if existing > 0:
        conn.close()
        return 0

    # Try Firestore first
    vehicles = _load_from_firestore(FS_VEHICLES_DOC)

    # Fall back to local JSON
    if not vehicles and VEHICLES_FILE.exists():
        try:
            vehicles = json.loads(VEHICLES_FILE.read_text())
            logger.info("Loaded vehicles from local JSON fallback")
        except Exception:
            vehicles = None

    if not vehicles:
        conn.close()
        return 0

    # Get column names from the vehicles table
    cursor = conn.execute("PRAGMA table_info(vehicles)")
    valid_columns = {row["name"] for row in cursor.fetchall()}

    restored = 0
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
    """Restore branding settings from Firestore (or JSON fallback) if DB is empty."""
    from utils.database import get_connection
    conn = get_connection()
    cursor = conn.execute("SELECT COUNT(*) as count FROM branding_settings")
    existing = cursor.fetchone()["count"]

    if existing > 0:
        conn.close()
        return False

    # Try Firestore first
    branding = _load_from_firestore(FS_BRANDING_DOC)

    # Fall back to local JSON
    if not branding and BRANDING_FILE.exists():
        try:
            branding = json.loads(BRANDING_FILE.read_text())
            logger.info("Loaded branding from local JSON fallback")
        except Exception:
            branding = None

    if not branding:
        conn.close()
        return False

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


def restore_media_library():
    """Restore media library records from Firestore (or JSON fallback) if DB is empty."""
    from utils.database import get_connection
    conn = get_connection()
    cursor = conn.execute("SELECT COUNT(*) as count FROM media_library")
    existing = cursor.fetchone()["count"]

    if existing > 0:
        conn.close()
        return 0

    # Try Firestore first
    items = _load_from_firestore(FS_MEDIA_LIBRARY_DOC)

    # Fall back to local JSON
    if not items and MEDIA_LIBRARY_FILE.exists():
        try:
            items = json.loads(MEDIA_LIBRARY_FILE.read_text())
            logger.info("Loaded media library from local JSON fallback")
        except Exception:
            items = None

    if not items:
        conn.close()
        return 0

    restored = 0
    for item in items:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO media_library (id, label, file_path, file_name, file_type, media_group, thumbnail_url, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (item.get("id"), item.get("label", ""), item.get("file_path", ""),
                 item.get("file_name", ""), item.get("file_type", "photo"),
                 item.get("media_group", ""), item.get("thumbnail_url"),
                 item.get("created_at", datetime.now().isoformat())),
            )
            restored += 1
        except Exception as e:
            logger.warning("Failed to restore media item %s: %s", item.get("id"), e)

    conn.commit()
    conn.close()
    logger.info("Restored %d media library records from backup", restored)
    return restored


def restore_people():
    """Restore people and their photos from Firestore (or JSON fallback) if DB is empty."""
    from utils.database import get_connection
    conn = get_connection()
    cursor = conn.execute("SELECT COUNT(*) as count FROM people")
    existing = cursor.fetchone()["count"]

    if existing > 0:
        conn.close()
        return 0

    # Try Firestore first
    people = _load_from_firestore(FS_PEOPLE_DOC)

    # Fall back to local JSON
    if not people and PEOPLE_FILE.exists():
        try:
            people = json.loads(PEOPLE_FILE.read_text())
            logger.info("Loaded people from local JSON fallback")
        except Exception:
            people = None

    if not people:
        conn.close()
        return 0

    restored = 0
    for person in people:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO people (id, name, created_at) VALUES (?, ?, ?)",
                (person["id"], person["name"], person.get("created_at", datetime.now().isoformat())),
            )
            for photo in person.get("photos", []):
                conn.execute(
                    "INSERT OR IGNORE INTO people_photos (id, person_id, file_path, file_name, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (photo["id"], photo["person_id"], photo["file_path"],
                     photo["file_name"], photo.get("created_at", datetime.now().isoformat())),
                )
            restored += 1
        except Exception as e:
            logger.warning("Failed to restore person %s: %s", person.get("name"), e)

    conn.commit()
    conn.close()
    logger.info("Restored %d people from backup", restored)
    return restored


def restore_all():
    """Restore all data from Firestore/JSON backups (only if DB tables are empty)."""
    templates = restore_prompt_templates()
    vehicles = restore_vehicles()
    branding = restore_branding()
    media = restore_media_library()
    people = restore_people()
    if templates or vehicles or branding or media or people:
        logger.info("Session data restored from persistent backup")
    return templates or vehicles or branding or media or people
