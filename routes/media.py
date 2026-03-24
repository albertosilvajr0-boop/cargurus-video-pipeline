"""Media library and people photo API routes."""

import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from config import settings
from utils.database import (
    save_media_item, get_all_media, get_media_groups,
    delete_media_item, delete_media_group, update_media_group_label,
    create_person, get_person, get_all_people, delete_person, update_person_name,
    save_people_photo, get_people_photo, get_photos_for_person, delete_people_photo,
)
from utils.cloud_storage import (
    is_gcs_enabled,
    upload_directory as gcs_upload_directory,
    upload_people_photo as gcs_upload_people_photo,
)
from utils.data_persistence import export_media_library
from utils.logger import get_logger

logger = get_logger("routes.media")

media_bp = Blueprint("media", __name__)


@media_bp.route("/api/media/upload", methods=["POST"])
def api_media_upload():
    """Upload photos/files to the media library."""
    files = request.files.getlist("files[]")
    has_photos = files and not all(f.filename == "" for f in files)
    has_sticker = request.files.get("sticker") and request.files.get("sticker").filename
    has_carfax = request.files.get("carfax") and request.files.get("carfax").filename
    if not has_photos and not has_sticker and not has_carfax:
        return jsonify({"error": "At least one file is required"}), 400

    label = request.form.get("label", "").strip()
    group = request.form.get("group", "").strip()
    if not group:
        group = f"media_{uuid.uuid4().hex[:10]}"

    group_dir = settings.MEDIA_DIR / group
    group_dir.mkdir(parents=True, exist_ok=True)

    saved_items = []
    for i, f in enumerate(files):
        if f.filename == "":
            continue
        ext = Path(f.filename).suffix.lower() or ".jpg"
        safe_name = f"file_{i:03d}{ext}"
        file_path = str(group_dir / safe_name)
        f.save(file_path)

        file_type = "photo"
        fname_lower = (f.filename or "").lower()
        if "sticker" in fname_lower:
            file_type = "sticker"
        elif "carfax" in fname_lower:
            file_type = "carfax"

        item_id = save_media_item(
            label=label or group, file_path=file_path,
            file_name=f.filename or safe_name, file_type=file_type,
            media_group=group,
        )
        saved_items.append({"id": item_id, "file_name": f.filename, "file_type": file_type})

    sticker = request.files.get("sticker")
    if sticker and sticker.filename:
        ext = Path(sticker.filename).suffix.lower() or ".jpg"
        safe_name = f"sticker{ext}"
        file_path = str(group_dir / safe_name)
        sticker.save(file_path)
        item_id = save_media_item(
            label=label or group, file_path=file_path,
            file_name=sticker.filename or safe_name, file_type="sticker",
            media_group=group,
        )
        saved_items.append({"id": item_id, "file_name": sticker.filename, "file_type": "sticker"})

    carfax = request.files.get("carfax")
    if carfax and carfax.filename:
        ext = Path(carfax.filename).suffix.lower() or ".jpg"
        safe_name = f"carfax{ext}"
        file_path = str(group_dir / safe_name)
        carfax.save(file_path)
        item_id = save_media_item(
            label=label or group, file_path=file_path,
            file_name=carfax.filename or safe_name, file_type="carfax",
            media_group=group,
        )
        saved_items.append({"id": item_id, "file_name": carfax.filename, "file_type": "carfax"})

    if is_gcs_enabled():
        gcs_upload_directory(str(group_dir), f"media/{group}")

    export_media_library()

    return jsonify({
        "status": "saved", "group": group, "label": label or group,
        "items": saved_items, "count": len(saved_items),
    })


@media_bp.route("/api/media")
def api_media_list():
    group = request.args.get("group")
    return jsonify(get_all_media(media_group=group))


@media_bp.route("/api/media/groups")
def api_media_groups():
    return jsonify(get_media_groups())


@media_bp.route("/api/media/<int:item_id>", methods=["DELETE"])
def api_media_delete(item_id):
    if delete_media_item(item_id):
        export_media_library()
        return jsonify({"status": "deleted"})
    return jsonify({"error": "Media item not found"}), 404


@media_bp.route("/api/media/group/<group_name>", methods=["DELETE"])
def api_media_delete_group(group_name):
    items = get_all_media(media_group=group_name)
    for item in items:
        try:
            Path(item["file_path"]).unlink(missing_ok=True)
        except Exception:
            logger.warning("Could not delete file: %s", item.get("file_path"))

    count = delete_media_group(group_name)

    group_dir = settings.MEDIA_DIR / group_name
    try:
        if group_dir.exists():
            import shutil
            shutil.rmtree(str(group_dir), ignore_errors=True)
    except Exception:
        logger.warning("Could not remove group directory: %s", group_dir)

    export_media_library()
    return jsonify({"status": "deleted", "count": count})


@media_bp.route("/api/media/group/<group_name>/rename", methods=["POST"])
def api_media_rename_group(group_name):
    data = request.get_json()
    new_label = data.get("label", "").strip() if data else ""
    if not new_label:
        return jsonify({"error": "label is required"}), 400
    update_media_group_label(group_name, new_label)
    export_media_library()
    return jsonify({"status": "updated"})


# --- People & People Photos ---

@media_bp.route("/api/people", methods=["GET"])
def api_people_list():
    return jsonify(get_all_people())


@media_bp.route("/api/people", methods=["POST"])
def api_people_create():
    data = request.get_json()
    if not data or not data.get("name", "").strip():
        return jsonify({"error": "A name is required"}), 400
    person_id = create_person(data["name"].strip())
    return jsonify({"status": "created", "id": person_id, "name": data["name"].strip()})


@media_bp.route("/api/people/<int:person_id>", methods=["PATCH"])
def api_people_update(person_id):
    data = request.get_json()
    if not data or not data.get("name", "").strip():
        return jsonify({"error": "A name is required"}), 400
    if update_person_name(person_id, data["name"].strip()):
        return jsonify({"status": "updated"})
    return jsonify({"error": "Person not found"}), 404


@media_bp.route("/api/people/<int:person_id>", methods=["DELETE"])
def api_people_delete(person_id):
    person = get_person(person_id)
    if person:
        photos = get_photos_for_person(person_id)
        for photo in photos:
            try:
                Path(photo["file_path"]).unlink(missing_ok=True)
            except Exception:
                logger.warning("Could not delete photo: %s", photo.get("file_path"))
    if delete_person(person_id):
        return jsonify({"status": "deleted"})
    return jsonify({"error": "Person not found"}), 404


@media_bp.route("/api/people/<int:person_id>/photos", methods=["POST"])
def api_people_photo_upload(person_id):
    if person_id == 0:
        name = request.form.get("name", "").strip()
        if not name:
            return jsonify({"error": "A name is required when creating a new person"}), 400
        person_id = create_person(name)
    else:
        person = get_person(person_id)
        if not person:
            return jsonify({"error": "Person not found"}), 404

    files = request.files.getlist("photos")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "At least one photo file is required"}), 400

    saved = []
    for photo in files:
        if not photo or photo.filename == "":
            continue
        ext = Path(photo.filename).suffix.lower() or ".jpg"
        safe_name = f"person_{person_id}_{uuid.uuid4().hex[:10]}{ext}"
        file_path = str(settings.PEOPLE_DIR / safe_name)
        photo.save(file_path)

        photo_id = save_people_photo(person_id=person_id, file_path=file_path, file_name=photo.filename or safe_name)
        if is_gcs_enabled():
            gcs_upload_people_photo(file_path, person_id)
        saved.append({"id": photo_id, "file_name": photo.filename})

    return jsonify({"status": "saved", "person_id": person_id, "photos": saved})


@media_bp.route("/api/people/photos/<int:photo_id>", methods=["DELETE"])
def api_people_photo_delete(photo_id):
    photo = get_people_photo(photo_id)
    if photo:
        try:
            Path(photo["file_path"]).unlink(missing_ok=True)
        except Exception:
            logger.warning("Could not delete photo file: %s", photo.get("file_path"))
    if delete_people_photo(photo_id):
        return jsonify({"status": "deleted"})
    return jsonify({"error": "Photo not found"}), 404


# --- File serving ---

@media_bp.route("/people/<path:filename>")
def serve_people_photo(filename):
    return send_from_directory(str(settings.PEOPLE_DIR), filename)


@media_bp.route("/media/<path:filename>")
def serve_media(filename):
    return send_from_directory(str(settings.MEDIA_DIR), filename)
