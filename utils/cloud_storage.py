"""Google Cloud Storage integration for persisting video files."""

from pathlib import Path

from config.settings import GCS_BUCKET_NAME, GCS_CREDENTIALS_PATH, GCS_PUBLIC_URL_BASE
from utils.logger import get_logger

logger = get_logger("cloud_storage")


def is_gcs_enabled() -> bool:
    """Check if GCS is configured."""
    return bool(GCS_BUCKET_NAME)


def _get_client():
    """Create a GCS client, using explicit credentials if provided."""
    from google.cloud import storage

    if GCS_CREDENTIALS_PATH:
        return storage.Client.from_service_account_json(GCS_CREDENTIALS_PATH)
    return storage.Client()


def upload_video(local_path: str, destination_blob: str | None = None) -> str | None:
    """Upload a video file to GCS.

    Args:
        local_path: Path to the local video file.
        destination_blob: GCS object name. Defaults to 'videos/<filename>'.

    Returns:
        Public URL of the uploaded video, or None on failure.
    """
    if not is_gcs_enabled():
        return None

    local = Path(local_path)
    if not local.exists():
        logger.error("Cannot upload — file not found: %s", local_path)
        return None

    if destination_blob is None:
        destination_blob = f"videos/{local.name}"

    try:
        client = _get_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(destination_blob)

        blob.upload_from_filename(str(local), content_type="video/mp4")
        logger.info("Uploaded %s -> gs://%s/%s", local.name, GCS_BUCKET_NAME, destination_blob)

        if GCS_PUBLIC_URL_BASE:
            url = f"{GCS_PUBLIC_URL_BASE.rstrip('/')}/{destination_blob}"
        else:
            url = blob.public_url

        return url
    except Exception as e:
        logger.error("GCS upload failed for %s: %s: %s", local_path, type(e).__name__, e)
        return None


def delete_video(blob_name: str) -> bool:
    """Delete a video from GCS."""
    if not is_gcs_enabled():
        return False

    try:
        client = _get_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(blob_name)
        blob.delete()
        logger.info("Deleted gs://%s/%s", GCS_BUCKET_NAME, blob_name)
        return True
    except Exception as e:
        logger.error("GCS delete failed for %s: %s: %s", blob_name, type(e).__name__, e)
        return False
