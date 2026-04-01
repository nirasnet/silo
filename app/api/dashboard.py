"""Dashboard API routes — /api/v1/dashboard/."""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from .auth import require_auth

router = APIRouter(prefix="/api/v1/dashboard")

TH_TZ = timezone(timedelta(hours=7))


def _get_db(request: Request):
    return request.app.db


def _now_th() -> datetime:
    return datetime.now(TH_TZ)


@router.get("/overview")
async def dashboard_overview(request: Request, auth: dict = Depends(require_auth)):
    """Organization overview using production date period."""
    db = _get_db(request)
    org_id = auth["org_id"]
    now = _now_th()

    # Use production period instead of calendar date
    period = db.get_production_period(org_id)
    start_iso = datetime.fromtimestamp(period["start_ts"], TH_TZ).isoformat()
    end_iso = datetime.fromtimestamp(period["end_ts"], TH_TZ).isoformat()

    messages_today = db.messages_count(org_id, date_from=start_iso, date_to=end_iso)
    groups = db.org_get_groups(org_id)
    active_groups = len([g for g in groups if g.get("status", "active") == "active"])
    digests_today = db.digest_count(org_id, date=period["production_date"])
    usage = db.usage_get_summary(org_id, month=now.strftime("%Y-%m"))

    org = db.org_get(org_id)
    org_name = org["name"] if org else ""
    plan = org.get("plan", "free") if org else "free"

    return {
        "org_id": org_id,
        "org_name": org_name,
        "plan": plan,
        "messages_today": messages_today,
        "active_groups": active_groups,
        "total_groups": len(groups),
        "digests_today": digests_today,
        "usage": usage,
        "production_date": period["production_date"],
        "production_period": f"{period['start_str']} — {period['end_str']}",
        "production_start_time": period["production_start_time"],
    }


@router.get("/groups")
async def dashboard_groups(request: Request, auth: dict = Depends(require_auth)):
    """All groups with stats using production date period."""
    db = _get_db(request)
    org_id = auth["org_id"]

    period = db.get_production_period(org_id)
    start_iso = datetime.fromtimestamp(period["start_ts"], TH_TZ).isoformat()
    end_iso = datetime.fromtimestamp(period["end_ts"], TH_TZ).isoformat()
    today = period["production_date"]

    groups = db.org_get_groups(org_id)
    result = []
    for g in groups:
        chat_id = g["group_mid"]
        msg_count = db.messages_count(org_id, chat_id=chat_id, date_from=start_iso, date_to=end_iso)
        last_msg = db.messages_get_recent(org_id, chat_id, limit=1)
        last_digest = db.digest_list(org_id, chat_id=chat_id, limit=1)

        result.append({
            "chat_id": chat_id,
            "name": g.get("group_name", chat_id[:16]),
            "status": g.get("status", "active"),
            "messages_today": msg_count,
            "total_messages": db.messages_count(org_id, chat_id=chat_id),
            "last_message": last_msg[0] if last_msg else None,
            "last_digest_date": last_digest[0].get("date") if last_digest else None,
            "has_digest_today": bool(last_digest and last_digest[0].get("date") == today),
        })

    # Sort by messages today (most active first)
    result.sort(key=lambda x: x["messages_today"], reverse=True)
    return result


@router.get("/groups/{chat_id}/messages")
async def group_messages(
    chat_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_auth),
):
    """Recent messages for a specific group."""
    db = _get_db(request)
    org_id = auth["org_id"]
    messages = db.messages_get_recent(org_id, chat_id, limit=limit, offset=offset)
    total = db.messages_count(org_id, chat_id=chat_id)
    return {
        "chat_id": chat_id,
        "messages": messages,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/groups/{chat_id}/digests")
async def group_digests(
    chat_id: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
    auth: dict = Depends(require_auth),
):
    """Digests for a specific group."""
    db = _get_db(request)
    org_id = auth["org_id"]
    digests = db.digest_list(org_id, chat_id=chat_id, limit=limit)
    return {
        "chat_id": chat_id,
        "digests": digests,
    }


@router.get("/activity")
async def activity_feed(
    request: Request,
    hours: int = Query(default=24, ge=1, le=168),
    limit: int = Query(default=50, ge=1, le=200),
    auth: dict = Depends(require_auth),
):
    """Activity feed: recent digests, messages, events."""
    db = _get_db(request)
    org_id = auth["org_id"]
    now = _now_th()
    since = (now - timedelta(hours=hours)).isoformat()

    # Get recent digests
    digests = db.digest_list(org_id, after="", limit=limit)
    recent_digests = [
        {
            "type": "digest",
            "timestamp": d.get("created_at", d.get("date", "")),
            "chat_id": d.get("chat_id", ""),
            "chat_name": d.get("chat_name", ""),
            "summary": (d.get("summary", "")[:120] + "...") if len(d.get("summary", "")) > 120 else d.get("summary", ""),
            "message_count": d.get("message_count", 0),
        }
        for d in digests
    ]

    # Get recent message activity (aggregate per group per hour)
    groups = db.org_get_groups(org_id)
    group_activity = []
    for g in groups:
        chat_id = g["group_mid"]
        count = db.messages_count(org_id, chat_id=chat_id, date_from=since, date_to=now.isoformat())
        if count > 0:
            group_activity.append({
                "type": "messages",
                "timestamp": now.isoformat(),
                "chat_id": chat_id,
                "chat_name": g.get("group_name", chat_id[:16]),
                "count": count,
            })

    # Merge and sort by timestamp (newest first)
    # Normalize timestamps: convert floats to ISO strings for consistent sorting
    feed = recent_digests + group_activity
    for item in feed:
        ts = item.get("timestamp", "")
        if isinstance(ts, (int, float)):
            from datetime import datetime, timezone
            item["timestamp"] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    feed.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)

    return {"feed": feed[:limit], "since": since}


@router.get("/discovered-groups")
async def discovered_groups(request: Request, auth: dict = Depends(require_auth)):
    """List groups discovered from messages but not yet formally added.
    Also includes already-added groups. This helps onboarding — users see
    all groups the bot has joined without needing to know MIDs."""
    db = _get_db(request)
    org_id = auth["org_id"]

    # Get all unique chat_ids from messages (groups bot has seen)
    stats = db.get_chat_stats(org_id)
    # Get formally added groups
    added = {g["group_mid"] for g in db.org_get_groups(org_id)}

    result = []
    for s in stats:
        chat_id = s["chat_id"]
        # Try to get group name from org_groups or from messages
        group = db.org_get_group_by_mid(org_id, chat_id)
        name = group["group_name"] if group and group.get("group_name") else chat_id[:20]
        result.append({
            "chat_id": chat_id,
            "name": name,
            "message_count": s.get("message_count", 0),
            "unique_senders": s.get("unique_senders", 0),
            "last_message": s.get("last_message", 0),
            "is_added": chat_id in added,
        })
    result.sort(key=lambda x: x["last_message"], reverse=True)
    return result


@router.post("/groups/{chat_id}/enable")
async def enable_group(chat_id: str, request: Request, auth: dict = Depends(require_auth)):
    """Enable a discovered group for auto-digest. Adds to org_groups if not already there."""
    db = _get_db(request)
    org_id = auth["org_id"]
    existing = db.org_get_group_by_mid(org_id, chat_id)
    if not existing:
        # Get name from LINE API if possible
        try:
            from app.line_oa.api import get_group_summary
            info = get_group_summary(chat_id)
            name = info.get("groupName", chat_id[:16]) if info else chat_id[:16]
        except Exception:
            name = chat_id[:16]
        db.org_add_group(org_id, chat_id, group_name=name)
    return {"ok": True, "chat_id": chat_id}


@router.post("/groups/{chat_id}/disable")
async def disable_group(chat_id: str, request: Request, auth: dict = Depends(require_auth)):
    """Remove a group from auto-digest."""
    db = _get_db(request)
    org_id = auth["org_id"]
    db.org_remove_group(org_id, chat_id)
    return {"ok": True}
