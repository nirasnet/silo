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
    """Generate AI digest for a chat."""
    db = request.app.db
    ai = request.app.ai
    org_id = body.org_id or auth["org_id"]
    now = datetime.now(TH_TZ)

    if body.last_24h:
        after_ts = (now - timedelta(hours=24)).timestamp()
        date_str = now.strftime("%Y-%m-%d")
    elif body.date:
        date_str = body.date
        dt = datetime.strptime(body.date, "%Y-%m-%d").replace(tzinfo=TH_TZ)
        after_ts = dt.replace(hour=0, minute=0, second=0).timestamp()
    else:
        date_str = now.strftime("%Y-%m-%d")
        after_ts = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    # Get messages
    messages = db.get_messages(org_id, body.chat_id, after=after_ts, limit=5000)
    if not messages:
        return {"ok": False, "error": "ไม่มีข้อความในช่วงเวลานี้", "message_count": 0}

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

    return {
        "ok": True,
        "date": date_str,
        "chat_name": chat_name,
        "message_count": len(messages),
        "digest": digest,
    }


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
