"""AI digest API routes — /api/v1/digest/."""

import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from .auth import require_auth

router = APIRouter(prefix="/api/v1/digest")

TH_TZ = timezone(timedelta(hours=7))


def _get_db(request: Request):
    return request.app.db


def _get_ai(request: Request):
    return request.app.ai


def _now_th() -> datetime:
    return datetime.now(TH_TZ)


# ── Request models ──

class GenerateDigestRequest(BaseModel):
    org_id: Optional[str] = None
    chat_id: str
    date: Optional[str] = None       # YYYY-MM-DD, default today
    last_24h: Optional[bool] = False
    force_vision: Optional[bool] = False


class GenerateAllRequest(BaseModel):
    org_id: Optional[str] = None
    date: Optional[str] = None
    force_vision: Optional[bool] = False


class AskRequest(BaseModel):
    org_id: Optional[str] = None
    chat_id: str
    message: str
    conversation_id: Optional[str] = None


class SendSummaryRequest(BaseModel):
    org_id: Optional[str] = None
    chat_id: str
    digest_id: Optional[str] = None
    message: Optional[str] = None  # custom message, or auto from digest


class PreviewSummaryRequest(BaseModel):
    org_id: Optional[str] = None
    chat_id: str
    digest_id: Optional[str] = None


# ── Generate digest ──

@router.post("/generate")
async def generate_digest(body: GenerateDigestRequest, request: Request, auth: dict = Depends(require_auth)):
    """Generate an AI digest for a specific chat."""
    db = _get_db(request)
    ai = _get_ai(request)
    org_id = body.org_id or auth["org_id"]
    now = _now_th()

    # Determine date range
    if body.last_24h:
        period_start = now - timedelta(hours=24)
        period_end = now
        date_str = now.strftime("%Y-%m-%d")
    elif body.date:
        date_str = body.date
        dt = datetime.strptime(body.date, "%Y-%m-%d").replace(tzinfo=TH_TZ)
        period_start = dt.replace(hour=0, minute=0, second=0)
        period_end = dt.replace(hour=23, minute=59, second=59)
    else:
        date_str = now.strftime("%Y-%m-%d")
        period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        period_end = now

    # Fetch messages for the period
    messages = db.messages_get_range(
        org_id, body.chat_id,
        start=period_start.isoformat(),
        end=period_end.isoformat(),
    )

    if not messages:
        return {"ok": False, "error": "No messages found for this period", "message_count": 0}

    # Get chat name
    chat_name = db.group_get_name(org_id, body.chat_id) or body.chat_id[:16]

    # Generate digest via AI provider
    try:
        result = ai.generate_digest(
            messages=messages,
            chat_name=chat_name,
            date=date_str,
            force_vision=body.force_vision or False,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI generation failed: {e}")

    # Save digest
    digest_id = db.digest_save(
        org_id=org_id,
        chat_id=body.chat_id,
        chat_name=chat_name,
        date=date_str,
        summary=result.get("summary", ""),
        topics=result.get("topics", []),
        action_items=result.get("action_items", []),
        key_decisions=result.get("key_decisions", []),
        sentiment=result.get("sentiment", "neutral"),
        message_count=len(messages),
        raw_json=result,
    )

    # Record usage
    db.usage_record(org_id, "digest", tokens=result.get("tokens_used", 0))

    return {
        "ok": True,
        "digest_id": digest_id,
        "chat_name": chat_name,
        "date": date_str,
        "message_count": len(messages),
        "summary": result.get("summary", ""),
        "topics": result.get("topics", []),
    }


@router.post("/generate/all")
async def generate_all_digests(body: GenerateAllRequest, request: Request, auth: dict = Depends(require_auth)):
    """Generate digests for all active groups in an org."""
    db = _get_db(request)
    ai = _get_ai(request)
    org_id = body.org_id or auth["org_id"]
    now = _now_th()
    date_str = body.date or now.strftime("%Y-%m-%d")

    groups = db.org_get_groups(org_id)
    if not groups:
        return {"ok": False, "error": "No groups connected", "results": []}

    results = []
    for group in groups:
        chat_id = group["group_mid"]
        chat_name = group.get("group_name", chat_id[:16])

        # Fetch messages
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TH_TZ)
        messages = db.messages_get_range(
            org_id, chat_id,
            start=dt.replace(hour=0, minute=0, second=0).isoformat(),
            end=dt.replace(hour=23, minute=59, second=59).isoformat(),
        )

        if not messages:
            results.append({"chat_id": chat_id, "chat_name": chat_name, "ok": False, "reason": "no messages"})
            continue

        try:
            result = ai.generate_digest(
                messages=messages, chat_name=chat_name,
                date=date_str, force_vision=body.force_vision or False,
            )
            digest_id = db.digest_save(
                org_id=org_id, chat_id=chat_id, chat_name=chat_name,
                date=date_str, summary=result.get("summary", ""),
                topics=result.get("topics", []),
                action_items=result.get("action_items", []),
                key_decisions=result.get("key_decisions", []),
                sentiment=result.get("sentiment", "neutral"),
                message_count=len(messages), raw_json=result,
            )
            db.usage_record(org_id, "digest", tokens=result.get("tokens_used", 0))
            results.append({"chat_id": chat_id, "chat_name": chat_name, "ok": True, "digest_id": digest_id, "message_count": len(messages)})
        except Exception as e:
            results.append({"chat_id": chat_id, "chat_name": chat_name, "ok": False, "reason": str(e)})

    success = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "total": len(results), "success": success, "results": results}


# ── List digests ──

@router.get("/list")
async def list_digests(
    request: Request,
    org_id: str = Query(default=""),
    chat_id: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=100),
    after: str = Query(default=""),
    auth: dict = Depends(require_auth),
):
    db = _get_db(request)
    resolved_org = org_id or auth["org_id"]
    return db.digest_list(resolved_org, chat_id=chat_id, limit=limit, after=after)


@router.get("/chats")
async def list_digest_chats(
    request: Request,
    org_id: str = Query(default=""),
    auth: dict = Depends(require_auth),
):
    """List chats that have digests with latest digest info."""
    db = _get_db(request)
    resolved_org = org_id or auth["org_id"]
    return db.digest_list_chats(resolved_org)


# ── AI Q&A ──

@router.post("/ask")
async def ask_ai(body: AskRequest, request: Request, auth: dict = Depends(require_auth)):
    """Ask an AI question about a chat, using digests as context."""
    db = _get_db(request)
    ai = _get_ai(request)
    org_id = body.org_id or auth["org_id"]

    # Get digests for context
    digests = db.digest_list(org_id, chat_id=body.chat_id, limit=30)
    chat_name = db.group_get_name(org_id, body.chat_id) or body.chat_id[:16]

    if not digests:
        # Fall back to recent messages
        messages = db.messages_get_recent(org_id, body.chat_id, limit=200)
        if not messages:
            return {"answer": "ไม่มีข้อมูลแชทสำหรับกลุ่มนี้", "conversation_id": None}
        transcript = "\n".join(f"[{m.get('sender_name', 'Unknown')}]: {m.get('text', '')}" for m in messages[-200:])
        digests = [{"date": "recent", "summary": f"ข้อความล่าสุด {len(messages)} ข้อความ:\n{transcript}"}]

    # Conversation history
    conv_id = body.conversation_id
    conversation_history = None
    if conv_id:
        conversation_history = db.ai_conversation_messages(conv_id)
    else:
        conv_id = db.ai_conversation_create(org_id, auth["user_id"], body.chat_id, chat_name)

    # Save user message
    db.ai_message_save(conv_id, "user", body.message)

    # Ask AI
    try:
        answer = ai.ask_question(
            question=body.message,
            digests=digests,
            chat_name=chat_name,
            conversation_history=conversation_history,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI query failed: {e}")

    # Save answer
    db.ai_message_save(conv_id, "assistant", answer)
    db.usage_record(org_id, "ai_query")

    return {
        "answer": answer,
        "conversation_id": conv_id,
        "chat_name": chat_name,
    }


@router.get("/conversations")
async def list_conversations(
    request: Request,
    org_id: str = Query(default=""),
    chat_id: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=100),
    auth: dict = Depends(require_auth),
):
    db = _get_db(request)
    resolved_org = org_id or auth["org_id"]
    return db.ai_conversation_list(resolved_org, chat_id=chat_id, limit=limit)


@router.get("/conversations/{conv_id}/messages")
async def get_conversation_messages(conv_id: str, request: Request, auth: dict = Depends(require_auth)):
    db = _get_db(request)
    messages = db.ai_conversation_messages(conv_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return messages


# ── LINE Push Summary ──

@router.post("/summary/preview")
async def preview_summary(body: PreviewSummaryRequest, request: Request, auth: dict = Depends(require_auth)):
    """Preview what the LINE push summary would look like."""
    db = _get_db(request)
    org_id = body.org_id or auth["org_id"]

    if body.digest_id:
        digest = db.digest_get(body.digest_id)
    else:
        digests = db.digest_list(org_id, chat_id=body.chat_id, limit=1)
        digest = digests[0] if digests else None

    if not digest:
        raise HTTPException(status_code=404, detail="No digest found")

    # Format as LINE message
    chat_name = digest.get("chat_name", body.chat_id[:16])
    summary = digest.get("summary", "")
    topics = digest.get("topics", [])
    date = digest.get("date", "")

    lines = [f"📋 สรุปแชท: {chat_name}", f"📅 {date}", ""]
    if summary:
        lines.append(summary)
        lines.append("")
    if topics:
        lines.append("📌 หัวข้อสำคัญ:")
        for t in topics[:5]:
            lines.append(f"  • {t}")

    message = "\n".join(lines)
    return {"message": message, "digest_id": digest.get("id", "")}


@router.post("/summary/send")
async def send_summary(body: SendSummaryRequest, request: Request, auth: dict = Depends(require_auth)):
    """Send summary to a LINE group via LINE OA push message."""
    db = _get_db(request)
    org_id = body.org_id or auth["org_id"]

    # Get org LINE config
    org = db.org_get(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    channel_token = org.get("line_channel_token")
    if not channel_token:
        raise HTTPException(status_code=400, detail="LINE OA not configured. Set channel token in settings.")

    # Get message content
    if body.message:
        message = body.message
    else:
        # Build from digest
        preview = await preview_summary(
            PreviewSummaryRequest(org_id=org_id, chat_id=body.chat_id, digest_id=body.digest_id),
            request, auth,
        )
        message = preview["message"]

    # Send via LINE Messaging API
    try:
        line_oa = request.app.line_oa
        result = await line_oa.push_message(
            channel_token=channel_token,
            to=body.chat_id,
            message=message,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LINE push failed: {e}")

    db.usage_record(org_id, "line_push")
    return {"ok": True, "message_length": len(message)}
