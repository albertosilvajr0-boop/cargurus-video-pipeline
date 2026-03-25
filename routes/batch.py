"""Batch/bulk video generation API routes.

Supports CSV upload of VINs and sequential 1-at-a-time processing
to avoid API rate limits and memory issues on Cloud Run.
"""

import csv
import io
import threading
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

from config import settings
from utils.database import get_prompt_template
from utils.job_store import get_job_store
from utils.logger import get_logger
from utils.vin_decoder import validate_vin
from workers.pipeline import run_vin_pipeline

logger = get_logger("routes.batch")

batch_bp = Blueprint("batch", __name__)

# Track active batch processes
_batch_lock = threading.Lock()
_active_batches: dict[str, dict] = {}


@batch_bp.route("/api/batch/upload-csv", methods=["POST"])
def api_batch_upload_csv():
    """Upload a CSV file of VINs for bulk video generation.

    CSV format: VIN,Price (header optional)
    Each row becomes a queued video generation job.
    Processing happens sequentially (1 at a time) to avoid API limits.

    Returns batch_id for tracking progress via SSE.
    """
    csv_file = request.files.get("csv")
    if not csv_file or not csv_file.filename:
        return jsonify({"error": "CSV file is required"}), 400

    # Read and parse CSV
    try:
        content = csv_file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))

        # Detect column names (case-insensitive)
        if not reader.fieldnames:
            # Try without header
            csv_file.seek(0)
            content = csv_file.read().decode("utf-8-sig")
            reader = csv.reader(io.StringIO(content))
            rows = []
            for row in reader:
                if row and len(row) >= 1:
                    vin = row[0].strip()
                    price = float(row[1].strip()) if len(row) > 1 and row[1].strip() else None
                    if vin and validate_vin(vin):
                        rows.append({"vin": validate_vin(vin), "price": price})
        else:
            # Map column names case-insensitively
            field_map = {f.lower().strip(): f for f in reader.fieldnames}
            vin_col = field_map.get("vin", field_map.get("vin_number", field_map.get("vin #", "")))
            price_col = field_map.get("price", field_map.get("msrp", field_map.get("asking_price", "")))

            rows = []
            for row in reader:
                vin_raw = row.get(vin_col, "").strip() if vin_col else ""
                clean_vin = validate_vin(vin_raw) if vin_raw else None
                if not clean_vin:
                    continue
                price = None
                if price_col and row.get(price_col, "").strip():
                    try:
                        price_str = row[price_col].strip().replace("$", "").replace(",", "")
                        price = float(price_str)
                    except ValueError:
                        pass
                rows.append({"vin": clean_vin, "price": price})

    except Exception as e:
        logger.error("CSV parse error: %s", e)
        return jsonify({"error": f"Failed to parse CSV: {e}"}), 400

    if not rows:
        return jsonify({"error": "No valid VINs found in CSV. Expected column: VIN (or first column)."}), 400

    # Deduplicate
    seen = set()
    unique_rows = []
    for row in rows:
        if row["vin"] not in seen:
            seen.add(row["vin"])
            unique_rows.append(row)
    rows = unique_rows

    if len(rows) > 500:
        return jsonify({"error": f"Too many VINs ({len(rows)}). Maximum 500 per batch."}), 400

    # Create batch
    batch_id = f"batch_{uuid.uuid4().hex[:12]}"

    # Get optional form parameters
    prompt_template_id = request.form.get("prompt_template_id")
    prompt_template = get_prompt_template(int(prompt_template_id)) if prompt_template_id else None
    dealer_phone = request.form.get("dealer_phone", "")
    dealer_address = request.form.get("dealer_address", "")
    cta_text = request.form.get("cta_text", "")

    batch_info = {
        "batch_id": batch_id,
        "total": len(rows),
        "completed": 0,
        "failed": 0,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "vins": [r["vin"] for r in rows],
    }

    with _batch_lock:
        _active_batches[batch_id] = batch_info

    # Start batch processing in background thread
    thread = threading.Thread(
        target=_process_batch,
        args=(batch_id, rows),
        kwargs={
            "prompt_template": prompt_template,
            "prompt_template_id": prompt_template_id,
            "dealer_phone": dealer_phone,
            "dealer_address": dealer_address,
            "cta_text": cta_text,
        },
        daemon=True,
    )
    thread.start()

    return jsonify({
        "batch_id": batch_id,
        "total": len(rows),
        "status": "processing",
        "message": f"Queued {len(rows)} vehicles for video generation (processing 1 at a time)",
    })


@batch_bp.route("/api/batch/vins", methods=["POST"])
def api_batch_vins():
    """Submit a list of VINs for bulk video generation.

    JSON body: {"vins": ["VIN1", "VIN2", ...], "prices": {"VIN1": 29999, ...}}
    """
    data = request.get_json()
    if not data or not data.get("vins"):
        return jsonify({"error": "vins list is required"}), 400

    vins = data["vins"]
    prices = data.get("prices", {})

    rows = []
    for vin_raw in vins:
        clean_vin = validate_vin(str(vin_raw).strip())
        if clean_vin:
            rows.append({"vin": clean_vin, "price": prices.get(vin_raw) or prices.get(clean_vin)})

    if not rows:
        return jsonify({"error": "No valid VINs provided"}), 400

    if len(rows) > 500:
        return jsonify({"error": f"Too many VINs ({len(rows)}). Maximum 500 per batch."}), 400

    batch_id = f"batch_{uuid.uuid4().hex[:12]}"

    prompt_template_id = data.get("prompt_template_id")
    prompt_template = get_prompt_template(int(prompt_template_id)) if prompt_template_id else None

    batch_info = {
        "batch_id": batch_id,
        "total": len(rows),
        "completed": 0,
        "failed": 0,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "vins": [r["vin"] for r in rows],
    }

    with _batch_lock:
        _active_batches[batch_id] = batch_info

    thread = threading.Thread(
        target=_process_batch,
        args=(batch_id, rows),
        kwargs={
            "prompt_template": prompt_template,
            "prompt_template_id": prompt_template_id,
            "dealer_phone": data.get("dealer_phone", ""),
            "dealer_address": data.get("dealer_address", ""),
            "cta_text": data.get("cta_text", ""),
        },
        daemon=True,
    )
    thread.start()

    return jsonify({
        "batch_id": batch_id,
        "total": len(rows),
        "status": "processing",
    })


@batch_bp.route("/api/batch/<batch_id>")
def api_batch_status(batch_id):
    """Get the status of a batch job."""
    with _batch_lock:
        batch = _active_batches.get(batch_id)
    if not batch:
        return jsonify({"error": "Batch not found"}), 404
    return jsonify(batch)


@batch_bp.route("/api/batches")
def api_list_batches():
    """List all batch jobs."""
    with _batch_lock:
        batches = list(_active_batches.values())
    batches.sort(key=lambda b: b.get("created_at", ""), reverse=True)
    return jsonify(batches)


def _process_batch(
    batch_id: str,
    rows: list[dict],
    prompt_template: dict | None = None,
    prompt_template_id: str | None = None,
    dealer_phone: str = "",
    dealer_address: str = "",
    cta_text: str = "",
):
    """Process a batch of VINs sequentially (1 at a time).

    This is the key design decision: sequential processing avoids:
    - Sora API rate limits
    - Memory exhaustion on Cloud Run (1GB limit)
    - Container crashes from concurrent FFmpeg processes
    """
    store = get_job_store()

    with _batch_lock:
        _active_batches[batch_id]["status"] = "processing"

    for i, row in enumerate(rows):
        vin = row["vin"]
        price = row.get("price")
        job_id = f"{batch_id}_{vin}_{uuid.uuid4().hex[:6]}"

        # Create job in store with batch reference
        store.create(job_id, {
            "status": "decoding",
            "progress": f"Processing vehicle {i + 1} of {len(rows)}: {vin}",
            "vin": vin,
            "batch_id": batch_id,
            "batch_index": i,
            "batch_total": len(rows),
        })

        # Update batch progress
        with _batch_lock:
            _active_batches[batch_id].update({
                "current_index": i,
                "current_vin": vin,
                "current_job_id": job_id,
                "percent": int(i / len(rows) * 100),
            })

        logger.info("Batch %s: Processing %d/%d — VIN %s", batch_id, i + 1, len(rows), vin)

        overrides = {
            "price": price,
            "dealer_phone": dealer_phone,
            "dealer_address": dealer_address,
            "cta_text": cta_text,
        }

        try:
            # Run the VIN pipeline synchronously (blocking, 1 at a time)
            run_vin_pipeline(
                job_id=job_id,
                vin=vin,
                overrides=overrides,
                prompt_template=prompt_template,
                prompt_template_id=prompt_template_id,
                jobs_lock=store.lock,
                active_jobs=store._jobs,
            )

            # Check result
            job_result = store.get(job_id)
            if job_result and job_result.get("status") == "complete":
                with _batch_lock:
                    _active_batches[batch_id]["completed"] = _active_batches[batch_id].get("completed", 0) + 1
            else:
                with _batch_lock:
                    _active_batches[batch_id]["failed"] = _active_batches[batch_id].get("failed", 0) + 1

        except Exception as e:
            logger.error("Batch %s: Error processing VIN %s: %s", batch_id, vin, e)
            store.update(job_id, status="error", progress=f"Error: {e}")
            with _batch_lock:
                _active_batches[batch_id]["failed"] = _active_batches[batch_id].get("failed", 0) + 1

    # Batch complete
    with _batch_lock:
        batch = _active_batches[batch_id]
        batch["status"] = "complete"
        batch["percent"] = 100
        batch["completed_at"] = datetime.now().isoformat()

    logger.info(
        "Batch %s complete: %d/%d succeeded, %d failed",
        batch_id, batch.get("completed", 0), len(rows), batch.get("failed", 0),
    )
