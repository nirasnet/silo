"""AI digest API routes — /api/v1/digest/."""

import json
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from .auth import require_auth

router = APIRouter(prefix="/api/v1/digest")

TH_TZ = timezone(timedelta(hours=7))


class GenerateDigestRequest(BaseModel):
    org_id: Optional[str] = None
    chat_id: str
    date: Optional[str] = None
    last_24h: Optional[bool] = False


class AskRequest(BaseModel):
    org_id: Optional[str] = None
    chat_id: str
    message: str
    conversation_id: Optional[str] = None


@router.post("/generate")
async def generate_digest_route(body: GenerateDigestRequest, request: Request, auth: dict = Depends(require_auth)):
    """Generate AI digest for a chat. Uses production date config."""
    db = request.app.db
    ai = request.app.ai
    org_id = body.org_id or auth["org_id"]

    if body.last_24h:
        now = datetime.now(TH_TZ)
        after_ts = (now - timedelta(hours=24)).timestamp()
        end_ts = now.timestamp()
        date_str = now.strftime("%Y-%m-%d")
        period_str = f"{(now - timedelta(hours=24)).strftime('%d/%m %H:%M')} - {now.strftime('%d/%m %H:%M')}"
    else:
        # Use production date (respects org's production_start_time)
        period = db.get_production_period(org_id, target_date=body.date or "")
        after_ts = period["start_ts"]
        end_ts = period["end_ts"]
        date_str = period["production_date"]
        period_str = f"{period['start_str']} - {period['end_str']}"

    # Get messages in production period
    all_msgs = db.get_messages(org_id, body.chat_id, after=after_ts, limit=5000)
    messages = [m for m in all_msgs if m.get("created_at", 0) <= end_ts]
    if not messages:
        return {"ok": False, "error": f"ไม่มีข้อความในช่วง {period_str}", "message_count": 0, "period": period_str}

    # Get chat name from org_groups
    group = db.org_get_group_by_mid(org_id, body.chat_id)
    chat_name = group["group_name"] if group and group.get("group_name") else body.chat_id[:16]

    # Generate digest
    digest = ai.generate_digest(chat_name, date_str, messages)
    if not digest:
        return {"ok": False, "error": "AI ไม่สามารถสร้าง digest ได้"}

    # Save
    db.save_digest(org_id, body.chat_id, chat_name, date_str, digest, len(messages))
    db.usage_record(org_id, "digest")

    # Auto-push LINE summary after digest
    pushed = False
    try:
        group_info = db.org_get_group_by_mid(org_id, body.chat_id)
        summary_level = (group_info or {}).get("summary_level", "normal")
        org = db.org_get(org_id)
        channel_token = (org or {}).get("line_channel_token") or None

        from app.ai.provider import generate_line_summary
        from app.line_oa.api import send_push
        summary_text = generate_line_summary(
            chat_name, messages, period_str.split(" - ")[0] if " - " in period_str else "",
            period_str.split(" - ")[1] if " - " in period_str else "", level=summary_level,
        )
        if summary_text:
            send_push(body.chat_id, [{"type": "text", "text": summary_text}], channel_token=channel_token)
            db.usage_record(org_id, "summary")
            pushed = True
            print(f"[LINE-PUSH] Digest summary sent to {chat_name} ({body.chat_id[:16]})")
    except Exception as e:
        print(f"[LINE-PUSH] Failed for {chat_name}: {e}")

    return {
        "ok": True,
        "date": date_str,
        "period": period_str,
        "chat_name": chat_name,
        "message_count": len(messages),
        "digest": digest,
        "line_pushed": pushed,
    }


@router.get("/production-period")
async def get_production_period(request: Request, date: str = "", auth: dict = Depends(require_auth)):
    """Get current production date and time range based on org config."""
    db = request.app.db
    return db.get_production_period(auth["org_id"], target_date=date)


@router.post("/generate/all")
async def generate_all(request: Request, auth: dict = Depends(require_auth)):
    """Generate digests for all active groups."""
    db = request.app.db
    ai = request.app.ai
    org_id = auth["org_id"]
    now = datetime.now(TH_TZ)
    date_str = now.strftime("%Y-%m-%d")
    after_ts = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    groups = db.org_get_groups(org_id)
    generated, skipped, errors = 0, 0, 0

    for g in groups:
        chat_id = g["group_mid"]
        chat_name = g.get("group_name", chat_id[:16])
        messages = db.get_messages(org_id, chat_id, after=after_ts, limit=5000)
        if len(messages) < 2:
            skipped += 1
            continue
        try:
            digest = ai.generate_digest(chat_name, date_str, messages)
            if digest:
                db.save_digest(org_id, chat_id, chat_name, date_str, digest, len(messages))
                db.usage_record(org_id, "digest")
                generated += 1
            else:
                errors += 1
        except Exception:
            errors += 1

    return {"ok": True, "generated": generated, "skipped": skipped, "errors": errors}


class PushSummaryRequest(BaseModel):
    chat_id: str
    org_id: Optional[str] = None
    digest_id: Optional[str] = None
    level: Optional[str] = "normal"


@router.post("/push-summary")
async def push_summary(body: PushSummaryRequest, request: Request, auth: dict = Depends(require_auth)):
    """Push a LINE summary for a chat. Uses latest digest period or specified digest."""
    db = request.app.db
    ai = request.app.ai
    org_id = body.org_id or auth["org_id"]
    org = db.org_get(org_id)
    channel_token = (org or {}).get("line_channel_token") or None

    # Get group info
    group = db.org_get_group_by_mid(org_id, body.chat_id)
    chat_name = group["group_name"] if group and group.get("group_name") else body.chat_id[:16]
    level = body.level or (group or {}).get("summary_level", "normal")

    # Get messages for the production period
    period = db.get_production_period(org_id)
    messages = db.get_messages(org_id, body.chat_id, after=period["start_ts"], limit=5000)
    messages = [m for m in messages if m.get("created_at", 0) <= period["end_ts"]]

    if not messages:
        return {"ok": False, "error": "No messages in current production period"}

    from app.ai.provider import generate_line_summary
    from app.line_oa.api import send_push

    summary_text = generate_line_summary(
        chat_name, messages, period["start_str"], period["end_str"], level=level,
    )
    if not summary_text:
        return {"ok": False, "error": "Failed to generate summary"}

    result = send_push(body.chat_id, [{"type": "text", "text": summary_text}], channel_token=channel_token)
    db.usage_record(org_id, "summary")

    return {
        "ok": True,
        "chat_name": chat_name,
        "level": level,
        "message_count": len(messages),
        "period": f"{period['start_str']} - {period['end_str']}",
        "summary_length": len(summary_text),
        "line_result": result,
    }


@router.get("/list")
async def list_digests(
    request: Request,
    chat_id: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=100),
    auth: dict = Depends(require_auth),
):
    db = request.app.db
    return db.get_digests(auth["org_id"], chat_id=chat_id, limit=limit)


@router.get("/chats")
async def list_digest_chats(request: Request, auth: dict = Depends(require_auth)):
    db = request.app.db
    return db.get_digest_chats(auth["org_id"])


@router.post("/ask")
async def ask_ai(body: AskRequest, request: Request, auth: dict = Depends(require_auth)):
    """Ask AI a question about a chat using digests as context."""
    db = request.app.db
    ai = request.app.ai
    org_id = body.org_id or auth["org_id"]

    digests = db.get_digests(org_id, chat_id=body.chat_id, limit=30)
    group = db.org_get_group_by_mid(org_id, body.chat_id)
    chat_name = group["group_name"] if group and group.get("group_name") else body.chat_id[:16]

    if not digests:
        messages = db.messages_get_recent(org_id, body.chat_id, limit=200)
        if not messages:
            return {"answer": "ไม่มีข้อมูลแชทสำหรับกลุ่มนี้", "conversation_id": None}
        transcript = "\n".join(f"[{m.get('sender_name', '?')}]: {m.get('text', '')}" for m in messages)
        digests = [{"date": "recent", "summary": transcript}]

    # Conversation
    conv_id = body.conversation_id
    history = None
    if conv_id:
        history = db.get_ai_messages(conv_id)
    else:
        conv_id = db.create_ai_conversation(org_id, body.chat_id, auth["user_id"])

    db.save_ai_message(conv_id, "user", body.message)

    answer = ai.ask_question(body.message, digests, chat_name=chat_name, conversation_history=history)

    db.save_ai_message(conv_id, "assistant", answer)
    db.usage_record(org_id, "qa")

    return {"answer": answer, "conversation_id": conv_id}
