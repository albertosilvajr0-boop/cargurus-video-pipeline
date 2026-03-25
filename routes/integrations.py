"""DMS/CRM integration framework and webhook endpoints.

Provides:
1. CSV/spreadsheet import for VINs
2. Webhook endpoint for DMS push notifications (new inventory)
3. API scaffold for future DMS integrations (CDK, DealerSocket, VinSolutions)

The webhook endpoint accepts standardized inventory payloads and
automatically queues vehicles for video generation.

Setup:
  1. Configure WEBHOOK_SECRET in environment variables
  2. Point your DMS webhook to POST /api/integrations/webhook/inventory
  3. Videos will be auto-generated for new inventory items
"""

import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

from config import settings
from utils.database import get_connection
from utils.logger import get_logger
from utils.vin_decoder import validate_vin

logger = get_logger("routes.integrations")

integrations_bp = Blueprint("integrations", __name__)

# Webhook security
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
AUTO_GENERATE = os.environ.get("DMS_AUTO_GENERATE", "false").lower() == "true"


def _verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Verify HMAC-SHA256 webhook signature."""
    if not WEBHOOK_SECRET:
        return True  # No secret configured = accept all (dev mode)
    expected = hmac.new(
        WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


# --- Webhook Endpoint ---

@integrations_bp.route("/api/integrations/webhook/inventory", methods=["POST"])
def api_webhook_inventory():
    """Receive inventory updates from DMS/CRM systems.

    Accepts a standardized payload format:
    {
        "event": "inventory.new" | "inventory.updated" | "inventory.removed",
        "vehicles": [
            {
                "vin": "1C4RJFAG5LC123456",
                "year": 2024,
                "make": "Ram",
                "model": "1500",
                "trim": "Laramie",
                "price": 42990,
                "mileage": 15000,
                "exterior_color": "Granite Crystal",
                "photos": ["https://example.com/photo1.jpg"],
                "stock_number": "A12345"
            }
        ],
        "source": "cdk" | "dealersocket" | "vinsolutions" | "custom",
        "dealer_id": "optional-dealer-id"
    }

    Security: Set WEBHOOK_SECRET env var. Include X-Webhook-Signature header.
    """
    # Verify signature
    signature = request.headers.get("X-Webhook-Signature", "")
    if WEBHOOK_SECRET and not _verify_webhook_signature(request.data, signature):
        logger.warning("Webhook signature verification failed")
        return jsonify({"error": "Invalid signature"}), 401

    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    event = data.get("event", "inventory.new")
    vehicles = data.get("vehicles", [])
    source = data.get("source", "webhook")

    if not vehicles:
        return jsonify({"error": "No vehicles in payload"}), 400

    # Process each vehicle
    processed = []
    errors = []

    for v in vehicles:
        vin = v.get("vin", "")
        clean_vin = validate_vin(vin) if vin else None

        if event == "inventory.removed":
            # Just log removals, don't delete videos
            logger.info("Inventory removed: VIN=%s (source=%s)", vin, source)
            processed.append({"vin": vin, "action": "noted_removal"})
            continue

        if not clean_vin:
            errors.append({"vin": vin, "error": "Invalid VIN"})
            continue

        # Save to inventory queue
        _save_to_inventory_queue(clean_vin, v, source, event)
        processed.append({"vin": clean_vin, "action": "queued"})

    # Auto-generate videos if configured
    if AUTO_GENERATE and processed:
        vins_to_generate = [p["vin"] for p in processed if p["action"] == "queued"]
        if vins_to_generate:
            _trigger_batch_generation(vins_to_generate)
            logger.info("Auto-triggered batch generation for %d vehicles", len(vins_to_generate))

    return jsonify({
        "status": "ok",
        "processed": len(processed),
        "errors": len(errors),
        "details": {"processed": processed, "errors": errors},
    })


@integrations_bp.route("/api/integrations/webhook/test", methods=["POST"])
def api_webhook_test():
    """Test webhook endpoint — echoes back the payload for debugging."""
    return jsonify({
        "status": "ok",
        "message": "Webhook test successful",
        "received": request.get_json(),
        "headers": {
            "content-type": request.headers.get("Content-Type"),
            "x-webhook-signature": request.headers.get("X-Webhook-Signature", "(none)"),
        },
    })


# --- Inventory Queue ---

@integrations_bp.route("/api/integrations/inventory-queue")
def api_inventory_queue():
    """List items in the inventory queue."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM inventory_queue ORDER BY received_at DESC LIMIT 100"
        )
        items = [dict(row) for row in cursor.fetchall()]
    except Exception:
        items = []
    finally:
        conn.close()
    return jsonify(items)


@integrations_bp.route("/api/integrations/inventory-queue/<int:item_id>/generate", methods=["POST"])
def api_queue_generate(item_id):
    """Generate a video for a queued inventory item."""
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT * FROM inventory_queue WHERE id = ?", (item_id,))
        item = cursor.fetchone()
        if not item:
            return jsonify({"error": "Item not found"}), 404

        vin = item["vin"]
        price = item.get("price")

        # Trigger single VIN generation
        from routes.batch import _process_batch
        import threading

        batch_id = f"dms_{uuid.uuid4().hex[:8]}"
        rows = [{"vin": vin, "price": price}]

        thread = threading.Thread(
            target=_process_batch,
            args=(batch_id, rows),
            daemon=True,
        )
        thread.start()

        # Mark as processing
        conn.execute(
            "UPDATE inventory_queue SET status = 'processing', updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), item_id),
        )
        conn.commit()

    finally:
        conn.close()

    return jsonify({"status": "generating", "vin": vin, "batch_id": batch_id})


# --- Integration Status ---

@integrations_bp.route("/api/integrations/status")
def api_integration_status():
    """Check integration configuration status."""
    return jsonify({
        "webhook_configured": bool(WEBHOOK_SECRET),
        "auto_generate": AUTO_GENERATE,
        "webhook_url": "/api/integrations/webhook/inventory",
        "supported_sources": ["cdk", "dealersocket", "vinsolutions", "custom"],
        "supported_events": ["inventory.new", "inventory.updated", "inventory.removed"],
        "payload_format": {
            "event": "inventory.new",
            "vehicles": [{"vin": "required", "year": "optional", "make": "optional", "price": "optional"}],
            "source": "your-system-name",
        },
    })


# --- Internal Helpers ---

def _save_to_inventory_queue(vin: str, vehicle_data: dict, source: str, event: str):
    """Save an incoming inventory item to the queue."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO inventory_queue (vin, vehicle_data, source, event, status, received_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (vin, json.dumps(vehicle_data), source, event, datetime.now().isoformat()),
        )
        conn.commit()
    except Exception as e:
        logger.warning("Failed to save to inventory queue: %s", e)
    finally:
        conn.close()


def _trigger_batch_generation(vins: list[str]):
    """Trigger batch video generation for a list of VINs."""
    import threading
    from routes.batch import _process_batch

    batch_id = f"auto_{uuid.uuid4().hex[:8]}"
    rows = [{"vin": vin, "price": None} for vin in vins]

    thread = threading.Thread(
        target=_process_batch,
        args=(batch_id, rows),
        daemon=True,
    )
    thread.start()
    logger.info("Auto-generation batch %s started for %d vehicles", batch_id, len(vins))
