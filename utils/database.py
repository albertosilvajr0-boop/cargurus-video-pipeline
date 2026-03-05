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
            video_engine TEXT,  -- veo or sora
            video_cost REAL DEFAULT 0.0,
            
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
    """)
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


def get_vehicles_by_status(status: str) -> list:
    """Get all vehicles with a given status."""
    conn = get_connection()
    cursor = conn.execute("SELECT * FROM vehicles WHERE status = ? ORDER BY id", (status,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_all_vehicles() -> list:
    """Get all vehicles."""
    conn = get_connection()
    cursor = conn.execute("SELECT * FROM vehicles ORDER BY id")
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
    stats["total_cost"] = cursor.fetchone()["total_cost"]
    
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
    return count


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
    return updated
