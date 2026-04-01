"""LINE Official Account webhook handler — multi-tenant."""

from __future__ import annotations

import hashlib
import hmac
import base64
import json
import logging
import time
import uuid

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

from app import db
from app.config import settings
from app.line_oa import api as line_api

log = logging.getLogger("silo.webhook")
router = APIRouter(tags=["LINE Webhook"])

# ── Sender name cache (per-process, cleared on restart) ──
_name_cache: dict[str, str] = {}


def _verify_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    """Validate X-Line-Signature using HMAC-SHA256 with the org's channel secret."""
    if not channel_secret:
        log.warning("No channel_secret configured, skipping signature verification")
        return True
    mac = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256)
    expected = base64.b64encode(mac.digest()).decode("utf-8")
    ok = hmac.compare_digest(expected, signature)
    if not ok:
        log.warning("Signature mismatch: got=%s... expected=%s...", signature[:20], expected[:20])
    return ok


def _resolve_org(destination: str) -> dict | None:
    """Resolve the organization from the webhook destination (LINE channel user ID).

    Tries org lookup by line_channel_id. Falls back to default OA if only one org exists.
    """
    if destination:
        org = db.org_get_by_channel_id(destination)
        if org:
            return org
    # Fallback: if there's exactly one org, use it (single-tenant bootstrap)
    orgs = db.org_list()
    if len(orgs) == 1:
        return orgs[0]
    return None


def _get_channel_secret(org: dict | None) -> str:
    """Get the channel secret: org-specific or platform default."""
    if org and org.get("line_channel_secret"):
        return org["line_channel_secret"]
    return settings.line_channel_secret


def _get_channel_token(org: dict | None) -> str | None:
    """Get the channel token: org-specific or platform default."""
    if org and org.get("line_channel_token"):
        return org["line_channel_token"]
    return settings.line_channel_token or None


def _get_chat_id(source: dict) -> str:
    """Extract chat ID from event source."""
    src_type = source.get("type", "")
    if src_type == "group":
        return source.get("groupId", "")
    elif src_type == "room":
        return source.get("roomId", "")
    return source.get("userId", "")


def _resolve_sender_name(source: dict, sender_id: str, channel_token: str | None) -> str:
    """Resolve sender display name via LINE API. Cached per session."""
    if not sender_id:
        return ""
    cache_key = f"{channel_token or 'default'}:{sender_id}"
    if cache_key in _name_cache:
        return _name_cache[cache_key]

    name = ""
    try:
        src_type = source.get("type", "")
        chat_id = _get_chat_id(source)
        if src_type == "group":
            profile = line_api.get_group_member_profile(chat_id, sender_id, channel_token)
        elif src_type == "room":
            # Rooms use the same profile endpoint pattern
            profile = line_api.get_profile(sender_id, channel_token)
        else:
            profile = line_api.get_profile(sender_id, channel_token)
        name = profile.get("displayName", "")
    except Exception as e:
        log.error("Name resolve error: %s", e)

    if not name:
        name = sender_id[:12]
    _name_cache[cache_key] = name
    return name


# ══════════════════════════════════════════════
#  Event handlers
# ══════════════════════════════════════════════

def _process_events(events: list[dict], org: dict | None) -> None:
    """Process webhook events in background."""
    org_id = org["id"] if org else ""
    token = _get_channel_token(org)

    for event in events:
        event_type = event.get("type", "")
        try:
            if event_type == "message":
                _handle_message(event, org_id, token)
            elif event_type == "join":
                _handle_join(event, org_id, token)
            elif event_type == "leave":
                _handle_leave(event, org_id)
            elif event_type == "follow":
                _handle_follow(event, org_id)
            elif event_type == "unfollow":
                _handle_unfollow(event, org_id)
            else:
                log.debug("Unhandled event type: %s", event_type)
        except Exception as exc:
            log.error("Error processing %s event: %s", event_type, exc)


def _handle_message(event: dict, org_id: str, channel_token: str | None) -> None:
    """Store an incoming message in the database."""
    source = event.get("source", {})
    message = event.get("message", {})
    msg_type = message.get("type", "text")
    msg_id = message.get("id", str(uuid.uuid4()))
    chat_id = _get_chat_id(source)
    sender_id = source.get("userId", "")
    sender_name = _resolve_sender_name(source, sender_id, channel_token)
    timestamp = event.get("timestamp", 0)
    created_at = timestamp / 1000.0 if timestamp else time.time()

    text = ""
    content_type = "NONE"
    image_url = ""
    content_metadata = "{}"

    if msg_type == "text":
        text = message.get("text", "")
        content_type = "NONE"
    elif msg_type == "image":
        content_type = "IMAGE"
        image_url = f"https://api-data.line.me/v2/bot/message/{msg_id}/content"
    elif msg_type == "sticker":
        content_type = "STICKER"
        pkg = message.get("packageId", "")
        stk = message.get("stickerId", "")
        content_metadata = json.dumps({"STKPKGID": pkg, "STKID": stk})
    elif msg_type == "file":
        content_type = "FILE"
        content_metadata = json.dumps({
            "FILE_NAME": message.get("fileName", ""),
            "FILE_SIZE": str(message.get("fileSize", 0)),
        })
    elif msg_type in ("video", "audio", "location"):
        content_type = msg_type.upper()
    else:
        content_type = msg_type.upper()

    db.save_message(
        org_id=org_id,
        chat_id=chat_id,
        sender_id=sender_id,
        sender_name=sender_name,
        text=text,
        content_type=content_type,
        content_metadata=content_metadata,
        image_url=image_url,
        created_at=created_at,
        msg_id=msg_id,
    )

    # Track usage
    if org_id:
        db.usage_record(org_id, "message", user_id=sender_id)

    log.info("Message saved: type=%s chat=%s sender=%s org=%s",
             msg_type, chat_id[:12], sender_id[:12], org_id[:8] if org_id else "none")


def _handle_join(event: dict, org_id: str, channel_token: str | None) -> None:
    """Bot was added to a group or room. Auto-register the group if org exists."""
    source = event.get("source", {})
    chat_id = _get_chat_id(source)
    src_type = source.get("type", "")
    log.info("Joined: type=%s chat=%s org=%s", src_type, chat_id, org_id[:8] if org_id else "none")

    if org_id and src_type == "group":
        # Fetch group name from LINE API
        group_name = ""
        try:
            summary = line_api.get_group_summary(chat_id, channel_token)
            group_name = summary.get("groupName", "")
        except Exception as e:
            log.error("Failed to get group summary: %s", e)

        # Auto-register group under the org
        existing = db.org_get_group_by_mid(org_id, chat_id)
        if not existing:
            db.org_add_group(org_id, chat_id, group_name=group_name)
            log.info("Auto-registered group %s (%s) for org %s", chat_id, group_name, org_id[:8])


def _handle_leave(event: dict, org_id: str) -> None:
    """Bot was removed from a group or room."""
    source = event.get("source", {})
    chat_id = _get_chat_id(source)
    log.info("Left: type=%s chat=%s org=%s", source.get("type"), chat_id, org_id[:8] if org_id else "none")


def _handle_follow(event: dict, org_id: str) -> None:
    """User added the bot as a friend."""
    source = event.get("source", {})
    user_id = source.get("userId", "")
    log.info("Follow: user=%s org=%s", user_id, org_id[:8] if org_id else "none")


def _handle_unfollow(event: dict, org_id: str) -> None:
    """User blocked or unfriended the bot."""
    source = event.get("source", {})
    user_id = source.get("userId", "")
    log.info("Unfollow: user=%s org=%s", user_id, org_id[:8] if org_id else "none")


# ══════════════════════════════════════════════
#  Route
# ══════════════════════════════════════════════

@router.post("/webhook/line")
async def line_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive LINE webhook events.

    LINE requires 200 within a few seconds, so processing is deferred.
    Multi-tenant: resolves org from the 'destination' field in the payload.
    """
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    # Parse payload first to get destination for org resolution
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    destination = payload.get("destination", "")
    org = _resolve_org(destination)

    # Verify signature with the org's channel secret (or default)
    channel_secret = _get_channel_secret(org)
    if not _verify_signature(body, signature, channel_secret):
        raise HTTPException(status_code=403, detail="Invalid signature")

    events = payload.get("events", [])
    if events:
        background_tasks.add_task(_process_events, events, org)
        log.info("Webhook received: %d event(s) org=%s", len(events),
                 org["slug"] if org else "unknown")

    return {"status": "ok"}
