"""Server-Sent Events (SSE) for real-time job progress updates.

Replaces the polling pattern (GET /api/job/<id> every 2-3 seconds)
with a persistent HTTP connection that pushes updates instantly.
"""

import json
import time

from flask import Blueprint, Response, request

from utils.job_store import get_job_store
from utils.logger import get_logger

logger = get_logger("routes.events")

events_bp = Blueprint("events", __name__)


@events_bp.route("/api/events/jobs")
def sse_all_jobs():
    """SSE stream for all active job updates.

    The client connects once and receives real-time updates for all jobs.
    Each event is a JSON object with the full job state.
    """
    def generate():
        store = get_job_store()
        last_snapshot = {}

        while True:
            try:
                current = store.get_all()

                # Only send if something changed
                if current != last_snapshot:
                    data = json.dumps(current, default=str)
                    yield f"event: jobs\ndata: {data}\n\n"
                    last_snapshot = current

                # Send heartbeat every 15 seconds to keep connection alive
                yield ": heartbeat\n\n"
                time.sleep(1.5)

            except GeneratorExit:
                logger.debug("SSE client disconnected (all jobs)")
                return
            except Exception as e:
                logger.warning("SSE error: %s", e)
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                return

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@events_bp.route("/api/events/job/<job_id>")
def sse_single_job(job_id):
    """SSE stream for a single job's progress updates.

    Useful when the UI is focused on one specific generation job.
    Automatically closes when the job reaches a terminal state.
    """
    def generate():
        store = get_job_store()
        last_data = None
        terminal_states = {"complete", "error"}
        idle_count = 0

        while True:
            try:
                job = store.get(job_id)

                if job is None:
                    yield f"event: error\ndata: {json.dumps({'error': 'Job not found'})}\n\n"
                    return

                # Only send if changed
                if job != last_data:
                    data = json.dumps(job, default=str)
                    yield f"event: job\ndata: {data}\n\n"
                    last_data = job
                    idle_count = 0

                    # Close stream when job is done
                    if job.get("status") in terminal_states:
                        yield f"event: done\ndata: {data}\n\n"
                        return
                else:
                    idle_count += 1

                # Heartbeat
                if idle_count % 10 == 0:
                    yield ": heartbeat\n\n"

                time.sleep(1.0)

            except GeneratorExit:
                logger.debug("SSE client disconnected (job %s)", job_id)
                return
            except Exception as e:
                logger.warning("SSE error for job %s: %s", job_id, e)
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                return

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@events_bp.route("/api/events/batch/<batch_id>")
def sse_batch_progress(batch_id):
    """SSE stream for batch job progress.

    Streams updates for all jobs belonging to a batch.
    """
    def generate():
        store = get_job_store()
        last_data = None

        while True:
            try:
                all_jobs = store.get_all()
                # Filter to jobs in this batch
                batch_jobs = {
                    jid: j for jid, j in all_jobs.items()
                    if j.get("batch_id") == batch_id
                }

                if batch_jobs != last_data:
                    # Compute batch-level progress
                    total = len(batch_jobs)
                    completed = sum(1 for j in batch_jobs.values() if j.get("status") in ("complete", "error"))
                    failed = sum(1 for j in batch_jobs.values() if j.get("status") == "error")
                    current_job = next(
                        (j for j in batch_jobs.values() if j.get("status") not in ("complete", "error", "pending")),
                        None,
                    )

                    batch_data = {
                        "batch_id": batch_id,
                        "total": total,
                        "completed": completed,
                        "failed": failed,
                        "percent": int(completed / total * 100) if total else 0,
                        "current_job": current_job,
                        "jobs": batch_jobs,
                    }

                    data = json.dumps(batch_data, default=str)
                    yield f"event: batch\ndata: {data}\n\n"
                    last_data = batch_jobs

                    # Close when all done
                    if total > 0 and completed >= total:
                        yield f"event: done\ndata: {data}\n\n"
                        return

                yield ": heartbeat\n\n"
                time.sleep(2.0)

            except GeneratorExit:
                return
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                return

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
