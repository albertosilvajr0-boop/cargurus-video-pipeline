"""Social media sharing and push-to-social API routes.

Provides share URL generation and deep-link creation for:
- Facebook
- Instagram (via share sheet / clipboard)
- TikTok (via share sheet / clipboard)
- YouTube Shorts (via upload link)
- Twitter/X
- Direct link / QR code

Note: Actual posting to social platforms requires OAuth tokens from the user.
This module generates shareable URLs and manages share tracking for analytics.
"""

import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

from config import settings
from utils.database import get_connection, update_vehicle_status
from utils.cloud_storage import is_gcs_enabled
from utils.logger import get_logger

logger = get_logger("routes.social")

social_bp = Blueprint("social", __name__)


# --- Share Link Generation ---

@social_bp.route("/api/share/<int:vehicle_id>", methods=["POST"])
def api_create_share(vehicle_id):
    """Generate shareable links for a vehicle's video.

    Returns platform-specific share URLs and a universal share link.
    Also tracks the share event for analytics.
    """
    conn = get_connection()
    cursor = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,))
    vehicle = cursor.fetchone()
    conn.close()

    if not vehicle:
        return jsonify({"error": "Vehicle not found"}), 404

    video_url = vehicle["video_url"]
    if not video_url:
        # Fall back to local URL
        video_path = vehicle["video_path"]
        if video_path:
            from pathlib import Path
            video_url = f"/videos/{Path(video_path).name}"
        else:
            return jsonify({"error": "No video available for this vehicle"}), 400

    # Build vehicle description
    parts = [str(vehicle["year"] or ""), vehicle["make"] or "", vehicle["model"] or "", vehicle["trim"] or ""]
    vehicle_name = " ".join(p for p in parts if p).strip()
    price_str = f"${vehicle['price']:,.0f}" if vehicle.get("price") else ""

    # Get caption from video script
    caption = ""
    if vehicle.get("video_script"):
        import json
        try:
            script = json.loads(vehicle["video_script"])
            caption = script.get("script", {}).get("caption", "")
        except (json.JSONDecodeError, TypeError):
            pass

    if not caption:
        caption = f"Check out this {vehicle_name}! {price_str} {settings.DEALER_NAME}"

    # Platform-specific share URLs
    data = request.get_json() or {}
    platform = data.get("platform", "all")

    # Universal share link (could be a landing page URL in the future)
    share_url = video_url if video_url.startswith("http") else f"{request.host_url.rstrip('/')}{video_url}"

    # Build encoded components
    from urllib.parse import quote
    encoded_caption = quote(caption)
    encoded_url = quote(share_url)

    share_links = {
        "video_url": share_url,
        "caption": caption,
        "vehicle_name": vehicle_name,
        "platforms": {
            "facebook": {
                "share_url": f"https://www.facebook.com/sharer/sharer.php?u={encoded_url}&quote={encoded_caption}",
                "instructions": "Opens Facebook share dialog with video link",
            },
            "twitter": {
                "share_url": f"https://twitter.com/intent/tweet?text={encoded_caption}&url={encoded_url}",
                "instructions": "Opens Twitter/X compose with caption and link",
            },
            "linkedin": {
                "share_url": f"https://www.linkedin.com/sharing/share-offsite/?url={encoded_url}",
                "instructions": "Opens LinkedIn share dialog",
            },
            "instagram": {
                "share_url": None,
                "download_url": share_url,
                "instructions": "Download the video and upload via Instagram app. Caption copied to clipboard.",
                "caption": caption,
            },
            "tiktok": {
                "share_url": None,
                "download_url": share_url,
                "instructions": "Download the video and upload via TikTok app. Caption copied to clipboard.",
                "caption": caption,
            },
            "youtube": {
                "share_url": "https://studio.youtube.com/",
                "download_url": share_url,
                "instructions": "Download the video and upload to YouTube Shorts via YouTube Studio.",
                "caption": caption,
                "title": f"{vehicle_name} | {settings.DEALER_NAME}",
            },
            "whatsapp": {
                "share_url": f"https://wa.me/?text={encoded_caption}%20{encoded_url}",
                "instructions": "Opens WhatsApp with caption and video link",
            },
            "email": {
                "share_url": f"mailto:?subject={quote(vehicle_name + ' - ' + settings.DEALER_NAME)}&body={encoded_caption}%0A%0A{encoded_url}",
                "instructions": "Opens email client with subject and video link",
            },
            "direct": {
                "share_url": share_url,
                "instructions": "Copy this link to share anywhere",
            },
        },
    }

    # Log the share event for analytics
    _log_share_event(vehicle_id, platform, share_url)

    if platform != "all" and platform in share_links["platforms"]:
        return jsonify({
            "video_url": share_url,
            "caption": caption,
            "platform": share_links["platforms"][platform],
        })

    return jsonify(share_links)


@social_bp.route("/api/share/<int:vehicle_id>/stats")
def api_share_stats(vehicle_id):
    """Get share statistics for a vehicle."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT platform, COUNT(*) as count FROM share_events "
            "WHERE vehicle_id = ? GROUP BY platform",
            (vehicle_id,),
        )
        by_platform = {row["platform"]: row["count"] for row in cursor.fetchall()}

        cursor = conn.execute(
            "SELECT COUNT(*) as total FROM share_events WHERE vehicle_id = ?",
            (vehicle_id,),
        )
        total = cursor.fetchone()["total"]
    except Exception:
        by_platform = {}
        total = 0
    finally:
        conn.close()

    return jsonify({
        "vehicle_id": vehicle_id,
        "total_shares": total,
        "by_platform": by_platform,
    })


def _log_share_event(vehicle_id: int, platform: str, share_url: str):
    """Record a share event in the database."""
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO share_events (vehicle_id, platform, share_url, created_at) "
            "VALUES (?, ?, ?, ?)",
            (vehicle_id, platform, share_url, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("Failed to log share event: %s", e)
