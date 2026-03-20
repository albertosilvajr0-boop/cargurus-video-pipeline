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


def upload_branding_asset(local_path: str) -> str | None:
    """Upload a branding asset (e.g. dealer logo) to GCS so it survives cold restarts.

    Stored under 'branding/<filename>' in the bucket.

    Returns:
        GCS blob name on success, or None on failure.
    """
    if not is_gcs_enabled():
        return None

    local = Path(local_path)
    if not local.exists():
        logger.error("Cannot upload branding asset — file not found: %s", local_path)
        return None

    destination_blob = f"branding/{local.name}"
    try:
        client = _get_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(destination_blob)

        # Detect content type from extension
        ext = local.suffix.lower()
        content_type = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml", ".webp": "image/webp",
        }.get(ext, "application/octet-stream")

        blob.upload_from_filename(str(local), content_type=content_type)
        logger.info("Uploaded branding asset %s -> gs://%s/%s", local.name, GCS_BUCKET_NAME, destination_blob)
        return destination_blob
    except Exception as e:
        logger.error("GCS branding upload failed for %s: %s: %s", local_path, type(e).__name__, e)
        return None


def download_branding_asset(blob_name: str, local_path: str) -> bool:
    """Download a branding asset from GCS to a local path.

    Used on startup to restore the dealer logo after a cold restart.

    Returns:
        True if downloaded successfully, False otherwise.
    """
    if not is_gcs_enabled():
        return False

    try:
        client = _get_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(blob_name)

        if not blob.exists():
            logger.warning("Branding asset not found in GCS: gs://%s/%s", GCS_BUCKET_NAME, blob_name)
            return False

        # Ensure parent directory exists
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)

        blob.download_to_filename(local_path)
        logger.info("Downloaded branding asset gs://%s/%s -> %s", GCS_BUCKET_NAME, blob_name, local_path)
        return True
    except Exception as e:
        logger.error("GCS branding download failed for %s: %s: %s", blob_name, type(e).__name__, e)
        return False


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
