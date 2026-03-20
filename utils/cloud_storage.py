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


def _upload_image(local_path: str, gcs_prefix: str) -> str | None:
    """Upload an image file to GCS under the given prefix.

    Returns:
        GCS blob name on success, or None on failure.
    """
    if not is_gcs_enabled():
        return None

    local = Path(local_path)
    if not local.exists():
        logger.error("Cannot upload — file not found: %s", local_path)
        return None

    destination_blob = f"{gcs_prefix.strip('/')}/{local.name}"
    try:
        client = _get_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(destination_blob)

        ext = local.suffix.lower()
        content_type = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml", ".webp": "image/webp",
        }.get(ext, "application/octet-stream")

        blob.upload_from_filename(str(local), content_type=content_type)
        logger.info("Uploaded %s -> gs://%s/%s", local.name, GCS_BUCKET_NAME, destination_blob)
        return destination_blob
    except Exception as e:
        logger.error("GCS upload failed for %s: %s: %s", local_path, type(e).__name__, e)
        return None


def upload_branding_asset(local_path: str) -> str | None:
    """Upload a branding asset (e.g. dealer logo) to GCS.

    Stored under 'branding/<filename>' in the bucket.
    """
    return _upload_image(local_path, "branding")


def upload_people_photo(local_path: str, person_id: int) -> str | None:
    """Upload a people photo to GCS under people/<person_id>/.

    Returns:
        GCS blob name on success, or None on failure.
    """
    return _upload_image(local_path, f"people/{person_id}")


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


def download_video(blob_name: str, local_path: str) -> bool:
    """Download a video from GCS to a local path.

    Used to restore _clip.mp4 files for re-overlay after cold restarts.

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
            logger.warning("Video not found in GCS: gs://%s/%s", GCS_BUCKET_NAME, blob_name)
            return False

        Path(local_path).parent.mkdir(parents=True, exist_ok=True)

        blob.download_to_filename(local_path)
        logger.info("Downloaded video gs://%s/%s -> %s", GCS_BUCKET_NAME, blob_name, local_path)
        return True
    except Exception as e:
        logger.error("GCS video download failed for %s: %s: %s", blob_name, type(e).__name__, e)
        return False


def upload_directory(local_dir: str, gcs_prefix: str) -> int:
    """Upload all files in a local directory to GCS under the given prefix.

    Args:
        local_dir: Path to the local directory.
        gcs_prefix: GCS prefix (e.g., 'uploads/upload_abc123' or 'media/group_xyz').

    Returns:
        Number of files uploaded successfully.
    """
    if not is_gcs_enabled():
        return 0

    local = Path(local_dir)
    if not local.is_dir():
        logger.error("Cannot upload directory — not found: %s", local_dir)
        return 0

    try:
        client = _get_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        uploaded = 0

        for file_path in local.rglob("*"):
            if not file_path.is_file():
                continue
            relative = file_path.relative_to(local)
            blob_name = f"{gcs_prefix}/{relative}"

            ext = file_path.suffix.lower()
            content_type = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
                ".pdf": "application/pdf", ".svg": "image/svg+xml",
            }.get(ext, "application/octet-stream")

            blob = bucket.blob(blob_name)
            blob.upload_from_filename(str(file_path), content_type=content_type)
            uploaded += 1

        logger.info("Uploaded %d files from %s -> gs://%s/%s/", uploaded, local_dir, GCS_BUCKET_NAME, gcs_prefix)
        return uploaded
    except Exception as e:
        logger.error("GCS directory upload failed for %s: %s: %s", local_dir, type(e).__name__, e)
        return 0


def download_directory(gcs_prefix: str, local_dir: str) -> int:
    """Download all blobs under a GCS prefix to a local directory.

    Args:
        gcs_prefix: GCS prefix (e.g., 'uploads/upload_abc123').
        local_dir: Local directory to download into.

    Returns:
        Number of files downloaded successfully.
    """
    if not is_gcs_enabled():
        return 0

    try:
        client = _get_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blobs = list(bucket.list_blobs(prefix=gcs_prefix))

        if not blobs:
            logger.info("No blobs found under gs://%s/%s", GCS_BUCKET_NAME, gcs_prefix)
            return 0

        local = Path(local_dir)
        downloaded = 0

        for blob in blobs:
            # Skip "directory" markers
            if blob.name.endswith("/"):
                continue

            # Strip the prefix to get the relative path
            relative = blob.name[len(gcs_prefix):].lstrip("/")
            if not relative:
                continue

            dest = local / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(dest))
            downloaded += 1

        logger.info("Downloaded %d files from gs://%s/%s -> %s", downloaded, GCS_BUCKET_NAME, gcs_prefix, local_dir)
        return downloaded
    except Exception as e:
        logger.error("GCS directory download failed for %s: %s: %s", gcs_prefix, type(e).__name__, e)
        return 0


def list_prefixes(gcs_prefix: str) -> list[str]:
    """List unique immediate sub-prefixes under a GCS prefix.

    E.g., list_prefixes('uploads/') might return ['uploads/upload_abc/', 'uploads/upload_def/'].

    Returns:
        List of sub-prefix strings.
    """
    if not is_gcs_enabled():
        return []

    try:
        client = _get_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        # Use delimiter to get "directory-like" listing
        iterator = bucket.list_blobs(prefix=gcs_prefix, delimiter="/")

        # Must consume the iterator to populate prefixes
        _ = list(iterator)
        return list(iterator.prefixes)
    except Exception as e:
        logger.error("GCS list_prefixes failed for %s: %s: %s", gcs_prefix, type(e).__name__, e)
        return []


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
