"""LINE Messaging API client for Official Accounts — multi-tenant support."""

from __future__ import annotations

import logging
from typing import Optional

import requests

from app.config import settings

log = logging.getLogger("silo.line")

BASE_URL = "https://api.line.me/v2/bot"
DATA_URL = "https://api-data.line.me/v2/bot"


def _headers(channel_token: str | None = None) -> dict[str, str]:
    """Build authorization headers. Uses per-org token if provided, else default."""
    token = channel_token or settings.line_channel_token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _data_headers(channel_token: str | None = None) -> dict[str, str]:
    """Headers for binary content download (no content-type)."""
    token = channel_token or settings.line_channel_token
    return {"Authorization": f"Bearer {token}"}


# ── Send messages ──

def send_text(to: str, text: str, channel_token: str | None = None) -> dict:
    """Push a text message to a user/group/room."""
    resp = requests.post(
        f"{BASE_URL}/message/push",
        headers=_headers(channel_token),
        json={"to": to, "messages": [{"type": "text", "text": text}]},
        timeout=10,
    )
    if not resp.ok:
        log.error("send_text failed (%d): %s", resp.status_code, resp.text[:200])
    return resp.json() if resp.ok else {"error": resp.status_code, "detail": resp.text}


def send_reply(reply_token: str, text: str, channel_token: str | None = None) -> dict:
    """Reply to a webhook event."""
    resp = requests.post(
        f"{BASE_URL}/message/reply",
        headers=_headers(channel_token),
        json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
        timeout=10,
    )
    if not resp.ok:
        log.error("send_reply failed (%d): %s", resp.status_code, resp.text[:200])
    return resp.json() if resp.ok else {"error": resp.status_code, "detail": resp.text}


def send_push(to: str, messages: list[dict], channel_token: str | None = None) -> dict:
    """Push multiple messages (up to 5) to a user/group/room."""
    resp = requests.post(
        f"{BASE_URL}/message/push",
        headers=_headers(channel_token),
        json={"to": to, "messages": messages[:5]},
        timeout=10,
    )
    if not resp.ok:
        log.error("send_push failed (%d): %s", resp.status_code, resp.text[:200])
    return resp.json() if resp.ok else {"error": resp.status_code, "detail": resp.text}


# ── Profile ──

def get_profile(user_id: str, channel_token: str | None = None) -> dict:
    """Get a user's LINE profile."""
    resp = requests.get(
        f"{BASE_URL}/profile/{user_id}",
        headers=_headers(channel_token),
        timeout=5,
    )
    if not resp.ok:
        log.error("get_profile failed (%d): %s", resp.status_code, resp.text[:200])
        return {}
    return resp.json()


def get_group_summary(group_id: str, channel_token: str | None = None) -> dict:
    """Get group name, icon, and member count."""
    resp = requests.get(
        f"{BASE_URL}/group/{group_id}/summary",
        headers=_headers(channel_token),
        timeout=5,
    )
    if not resp.ok:
        log.error("get_group_summary failed (%d): %s", resp.status_code, resp.text[:200])
        return {}
    return resp.json()


def get_group_member_profile(
    group_id: str, user_id: str, channel_token: str | None = None,
) -> dict:
    """Get a member's profile within a group."""
    resp = requests.get(
        f"{BASE_URL}/group/{group_id}/member/{user_id}",
        headers=_headers(channel_token),
        timeout=5,
    )
    if not resp.ok:
        log.error("get_group_member_profile failed (%d): %s", resp.status_code, resp.text[:200])
        return {}
    return resp.json()


def get_content(message_id: str, channel_token: str | None = None) -> Optional[bytes]:
    """Download message content (image/file/audio). Returns raw bytes or None."""
    resp = requests.get(
        f"{DATA_URL}/message/{message_id}/content",
        headers=_data_headers(channel_token),
        stream=True,
        timeout=30,
    )
    if not resp.ok:
        log.error("get_content failed (%d): %s", resp.status_code, resp.text[:200])
        return None
    return resp.content


def get_group_members_count(group_id: str, channel_token: str | None = None) -> int:
    """Get the number of members in a group."""
    resp = requests.get(
        f"{BASE_URL}/group/{group_id}/members/count",
        headers=_headers(channel_token),
        timeout=5,
    )
    if not resp.ok:
        log.error("get_group_members_count failed (%d): %s", resp.status_code, resp.text[:200])
        return 0
    return resp.json().get("count", 0)
