"""Persistent job tracking that survives Cloud Run container restarts.

Replaces the in-memory _active_jobs dict with a Firestore-backed store.
Falls back to in-memory tracking if Firestore is unavailable.
"""

import threading
from datetime import datetime

from utils.logger import get_logger

logger = get_logger("job_store")

# Firestore collection for jobs
FS_JOBS_COLLECTION = "pipeline_jobs"


class JobStore:
    """Thread-safe job store with optional Firestore persistence.

    Jobs are always stored in-memory for fast access.
    If Firestore is available, jobs are also persisted there.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._firestore_enabled = False
        self._fs_client = None

        # Try to connect to Firestore
        try:
            from utils.data_persistence import _get_firestore
            client = _get_firestore()
            if client:
                self._fs_client = client
                self._firestore_enabled = True
                self._restore_active_jobs()
                logger.info("JobStore: Firestore persistence enabled")
            else:
                logger.info("JobStore: Running in memory-only mode")
        except Exception as e:
            logger.warning("JobStore: Firestore unavailable, running in memory-only mode: %s", e)

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    def _restore_active_jobs(self):
        """Restore incomplete jobs from Firestore on startup."""
        if not self._fs_client:
            return
        try:
            docs = (
                self._fs_client.collection(FS_JOBS_COLLECTION)
                .where("status", "not-in", ["complete", "error"])
                .stream()
            )
            count = 0
            for doc in docs:
                job_data = doc.to_dict()
                self._jobs[doc.id] = job_data
                count += 1
            if count:
                logger.info("Restored %d active jobs from Firestore", count)
        except Exception as e:
            logger.warning("Failed to restore jobs from Firestore: %s", e)

    def create(self, job_id: str, initial_data: dict) -> dict:
        """Create a new job entry."""
        job = {
            "status": "pending",
            "progress": "",
            "percent": 0,
            "vehicle_id": None,
            "started_at": datetime.now().isoformat(),
            **initial_data,
        }
        with self._lock:
            self._jobs[job_id] = job
        self._persist(job_id, job)
        return job

    def update(self, job_id: str, **kwargs):
        """Update fields on an existing job."""
        with self._lock:
            if job_id not in self._jobs:
                return
            self._jobs[job_id].update(kwargs)
            job = dict(self._jobs[job_id])
        self._persist(job_id, job)

    def get(self, job_id: str) -> dict | None:
        """Get a single job by ID."""
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def get_all(self) -> dict[str, dict]:
        """Get all jobs as {job_id: job_data}."""
        with self._lock:
            return {jid: dict(j, job_id=jid) for jid, j in self._jobs.items()}

    def get_active(self) -> dict[str, dict]:
        """Get only non-terminal jobs."""
        terminal = {"complete", "error"}
        with self._lock:
            return {
                jid: dict(j, job_id=jid)
                for jid, j in self._jobs.items()
                if j.get("status") not in terminal
            }

    def cleanup_old(self, max_completed: int = 100):
        """Remove oldest completed/error jobs if we exceed max_completed."""
        with self._lock:
            terminal = [
                (jid, j) for jid, j in self._jobs.items()
                if j.get("status") in ("complete", "error")
            ]
            if len(terminal) <= max_completed:
                return
            # Sort by started_at, remove oldest
            terminal.sort(key=lambda x: x[1].get("started_at", ""))
            to_remove = terminal[:len(terminal) - max_completed]
            for jid, _ in to_remove:
                del self._jobs[jid]
                self._delete_persisted(jid)

    def _persist(self, job_id: str, job: dict):
        """Persist job to Firestore (non-blocking)."""
        if not self._firestore_enabled:
            return
        try:
            # Convert any non-serializable values
            clean_job = {k: v for k, v in job.items() if v is not None}
            self._fs_client.collection(FS_JOBS_COLLECTION).document(job_id).set(clean_job)
        except Exception as e:
            logger.debug("Failed to persist job %s to Firestore: %s", job_id, e)

    def _delete_persisted(self, job_id: str):
        """Delete a job from Firestore."""
        if not self._firestore_enabled:
            return
        try:
            self._fs_client.collection(FS_JOBS_COLLECTION).document(job_id).delete()
        except Exception as e:
            logger.debug("Failed to delete job %s from Firestore: %s", job_id, e)


# Module-level singleton
_job_store: JobStore | None = None
_init_lock = threading.Lock()


def get_job_store() -> JobStore:
    """Get or create the global JobStore singleton."""
    global _job_store
    if _job_store is not None:
        return _job_store
    with _init_lock:
        if _job_store is not None:
            return _job_store
        _job_store = JobStore()
        return _job_store
