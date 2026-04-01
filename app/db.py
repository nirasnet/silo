"""Silo — SQLite database layer for multi-tenant LINE chat AI digest SaaS."""

import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta

from .config import settings

# Parse DB path from sqlite URL
_raw = settings.database_url
DB_PATH = _raw.replace("sqlite:///", "") if _raw.startswith("sqlite:///") else "data/silo.db"

_local = threading.local()
_BKK = timezone(timedelta(hours=7))


def _conn() -> sqlite3.Connection:
    """Get thread-local SQLite connection with WAL mode."""
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def _now() -> float:
    return time.time()


def _today() -> str:
    return datetime.now(_BKK).strftime("%Y-%m-%d")


def _month() -> str:
    return datetime.now(_BKK).strftime("%Y-%m")


# ══════════════════════════════════════════════
#  Schema initialization
# ══════════════════════════════════════════════

def init_db() -> None:
    """Create all tables if they don't exist."""
    c = _conn()
    c.executescript("""
        -- Multi-tenant organizations
        CREATE TABLE IF NOT EXISTS organizations (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            plan TEXT DEFAULT 'free',
            line_channel_id TEXT DEFAULT '',
            line_channel_secret TEXT DEFAULT '',
            line_channel_token TEXT DEFAULT '',
            max_groups INTEGER DEFAULT 1,
            max_users INTEGER DEFAULT 3,
            logo_url TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS org_members (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT '',
            role TEXT DEFAULT 'member',
            email TEXT DEFAULT '',
            display_name TEXT DEFAULT '',
            line_mid TEXT DEFAULT '',
            invited_by TEXT DEFAULT '',
            created_at REAL NOT NULL,
            UNIQUE(org_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS org_groups (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            group_mid TEXT NOT NULL,
            group_name TEXT DEFAULT '',
            auto_digest INTEGER DEFAULT 1,
            vision_enabled INTEGER DEFAULT 0,
            summary_level TEXT DEFAULT 'normal',
            summary_schedule TEXT DEFAULT '',
            created_at REAL NOT NULL,
            UNIQUE(org_id, group_mid)
        );

        -- Messages (multi-tenant)
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            sender_name TEXT NOT NULL DEFAULT '',
            text TEXT DEFAULT '',
            content_type TEXT DEFAULT 'NONE',
            content_metadata TEXT DEFAULT '{}',
            image_url TEXT DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_msg_org_chat ON messages (org_id, chat_id, created_at);

        -- AI digests
        CREATE TABLE IF NOT EXISTS digests (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            chat_name TEXT DEFAULT '',
            date TEXT NOT NULL,
            digest_json TEXT DEFAULT '{}',
            message_count INTEGER DEFAULT 0,
            image_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_digest_unique ON digests (org_id, chat_id, date);

        -- AI conversations (Q&A)
        CREATE TABLE IF NOT EXISTS ai_conversations (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            chat_id TEXT NOT NULL DEFAULT '',
            user_id TEXT NOT NULL DEFAULT '',
            title TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ai_conv_org ON ai_conversations (org_id, updated_at DESC);

        CREATE TABLE IF NOT EXISTS ai_messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ai_msg_conv ON ai_messages (conversation_id, created_at);

        -- Usage tracking
        CREATE TABLE IF NOT EXISTS usage_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id TEXT NOT NULL,
            user_id TEXT DEFAULT '',
            metric_type TEXT NOT NULL,
            count INTEGER DEFAULT 1,
            date TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_usage_org ON usage_metrics (org_id, date);

        -- Plans
        CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            price_thb INTEGER NOT NULL,
            max_groups INTEGER NOT NULL,
            max_users INTEGER NOT NULL,
            max_digests_per_day INTEGER NOT NULL,
            max_messages_per_month INTEGER NOT NULL,
            features_json TEXT DEFAULT '[]',
            created_at REAL NOT NULL
        );

        -- API tokens (per-org)
        CREATE TABLE IF NOT EXISTS api_tokens (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            token TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_token_org ON api_tokens (org_id);
    """)
    c.commit()


# ══════════════════════════════════════════════
#  Organizations
# ══════════════════════════════════════════════

def org_create(name: str, slug: str, plan: str = "free") -> dict:
    """Create a new organization. Returns the created org dict."""
    c = _conn()
    org_id = secrets.token_hex(8)
    now = _now()
    c.execute(
        "INSERT INTO organizations (id, name, slug, plan, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (org_id, name, slug, plan, now, now),
    )
    c.commit()
    return {"id": org_id, "name": name, "slug": slug, "plan": plan,
            "status": "active", "created_at": now, "updated_at": now}


def org_get(org_id: str) -> dict | None:
    row = _conn().execute("SELECT * FROM organizations WHERE id=?", (org_id,)).fetchone()
    return dict(row) if row else None


def org_get_by_slug(slug: str) -> dict | None:
    row = _conn().execute("SELECT * FROM organizations WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def org_get_by_channel_id(channel_id: str) -> dict | None:
    """Look up an organization by its LINE channel ID (destination)."""
    row = _conn().execute(
        "SELECT * FROM organizations WHERE line_channel_id=?", (channel_id,)
    ).fetchone()
    return dict(row) if row else None


def org_list() -> list[dict]:
    rows = _conn().execute("SELECT * FROM organizations ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def org_update(org_id: str, **kwargs: object) -> None:
    """Update allowed organization fields."""
    allowed = {
        "name", "slug", "plan", "line_channel_id", "line_channel_secret",
        "line_channel_token", "max_groups", "max_users", "logo_url", "status",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [org_id]
    c = _conn()
    c.execute(f"UPDATE organizations SET {set_clause} WHERE id=?", values)
    c.commit()


# ══════════════════════════════════════════════
#  Org members
# ══════════════════════════════════════════════

def org_add_member(
    org_id: str, user_id: str, role: str = "member",
    email: str = "", display_name: str = "", line_mid: str = "",
    invited_by: str = "",
) -> str:
    """Add a member to an organization. Returns member record ID."""
    c = _conn()
    member_id = secrets.token_hex(8)
    c.execute(
        "INSERT OR IGNORE INTO org_members (id, org_id, user_id, role, email, display_name, line_mid, invited_by, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (member_id, org_id, user_id, role, email, display_name, line_mid, invited_by, _now()),
    )
    c.commit()
    return member_id


def org_remove_member(org_id: str, user_id: str) -> None:
    c = _conn()
    c.execute("DELETE FROM org_members WHERE org_id=? AND user_id=?", (org_id, user_id))
    c.commit()


def org_get_members(org_id: str) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM org_members WHERE org_id=? ORDER BY created_at", (org_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def org_get_member_by_line_mid(org_id: str, line_mid: str) -> dict | None:
    row = _conn().execute(
        "SELECT * FROM org_members WHERE org_id=? AND line_mid=?", (org_id, line_mid)
    ).fetchone()
    return dict(row) if row else None


# ══════════════════════════════════════════════
#  Org groups
# ══════════════════════════════════════════════

def org_add_group(
    org_id: str, group_mid: str, group_name: str = "",
    auto_digest: bool = True, vision_enabled: bool = False,
    summary_level: str = "normal", summary_schedule: str = "",
) -> str:
    """Register a LINE group under an organization. Returns record ID."""
    c = _conn()
    gid = secrets.token_hex(8)
    c.execute(
        "INSERT OR IGNORE INTO org_groups (id, org_id, group_mid, group_name, auto_digest, vision_enabled, summary_level, summary_schedule, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (gid, org_id, group_mid, group_name, int(auto_digest), int(vision_enabled),
         summary_level, summary_schedule, _now()),
    )
    c.commit()
    return gid


def org_remove_group(org_id: str, group_mid: str) -> None:
    c = _conn()
    c.execute("DELETE FROM org_groups WHERE org_id=? AND group_mid=?", (org_id, group_mid))
    c.commit()


def org_get_groups(org_id: str) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM org_groups WHERE org_id=? ORDER BY created_at", (org_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def org_get_group_by_mid(org_id: str, group_mid: str) -> dict | None:
    """Look up a specific group within an org."""
    row = _conn().execute(
        "SELECT * FROM org_groups WHERE org_id=? AND group_mid=?", (org_id, group_mid)
    ).fetchone()
    return dict(row) if row else None


def org_find_group_any(group_mid: str) -> dict | None:
    """Find a group across all organizations (for webhook routing)."""
    row = _conn().execute(
        "SELECT og.*, o.id as found_org_id FROM org_groups og "
        "JOIN organizations o ON og.org_id = o.id "
        "WHERE og.group_mid=? LIMIT 1",
        (group_mid,),
    ).fetchone()
    return dict(row) if row else None


# ══════════════════════════════════════════════
#  Messages
# ══════════════════════════════════════════════

def save_message(
    org_id: str, chat_id: str, sender_id: str, sender_name: str = "",
    text: str = "", content_type: str = "NONE", content_metadata: str = "{}",
    image_url: str = "", created_at: float = 0, msg_id: str = "",
) -> None:
    """Insert a message. Duplicates are silently ignored."""
    if not chat_id:
        return
    c = _conn()
    c.execute(
        "INSERT OR IGNORE INTO messages (id, org_id, chat_id, sender_id, sender_name, text, content_type, content_metadata, image_url, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (msg_id or uuid.uuid4().hex[:16], org_id, chat_id, sender_id,
         sender_name or "", text or "", content_type or "NONE",
         content_metadata or "{}", image_url or "", created_at or _now()),
    )
    c.commit()


def get_messages(
    org_id: str, chat_id: str, after: float | None = None, limit: int = 5000,
) -> list[dict]:
    """Get messages for a chat within an org, ordered by time ascending."""
    c = _conn()
    if after:
        rows = c.execute(
            "SELECT * FROM messages WHERE org_id=? AND chat_id=? AND created_at>? ORDER BY created_at ASC LIMIT ?",
            (org_id, chat_id, after, limit),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM messages WHERE org_id=? AND chat_id=? ORDER BY created_at ASC LIMIT ?",
            (org_id, chat_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_image_messages(
    org_id: str, chat_id: str, after: float | None = None, limit: int = 20,
) -> list[dict]:
    """Get IMAGE messages for vision analysis."""
    c = _conn()
    conditions = ["org_id=?", "chat_id=?", "content_type='IMAGE'"]
    params: list = [org_id, chat_id]
    if after:
        conditions.append("created_at>?")
        params.append(after)
    where = " AND ".join(conditions)
    params.append(limit)
    rows = c.execute(
        f"SELECT id, sender_name, created_at FROM messages WHERE {where} ORDER BY created_at ASC LIMIT ?",
        params,
    ).fetchall()
    return [{"id": r["id"], "sender": r["sender_name"], "time": r["created_at"]} for r in rows]


def messages_count(org_id: str, chat_id: str = "", date_from: str = "", date_to: str = "") -> int:
    """Count messages with optional filters."""
    q = "SELECT COUNT(*) FROM messages WHERE org_id=?"
    p: list = [org_id]
    if chat_id:
        q += " AND chat_id=?"
        p.append(chat_id)
    if date_from:
        q += " AND created_at>=?"
        try:
            from datetime import datetime
            p.append(datetime.fromisoformat(date_from).timestamp())
        except Exception:
            p.append(0)
    if date_to:
        q += " AND created_at<=?"
        try:
            from datetime import datetime
            p.append(datetime.fromisoformat(date_to).timestamp())
        except Exception:
            p.append(9999999999)
    return _conn().execute(q, p).fetchone()[0]


def messages_get_recent(org_id: str, chat_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """Get recent messages for a chat."""
    rows = _conn().execute(
        "SELECT * FROM messages WHERE org_id=? AND chat_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (org_id, chat_id, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def digest_count(org_id: str, date: str = "") -> int:
    """Count digests, optionally for a specific date."""
    if date:
        return _conn().execute(
            "SELECT COUNT(*) FROM digests WHERE org_id=? AND date=?", (org_id, date)
        ).fetchone()[0]
    return _conn().execute(
        "SELECT COUNT(*) FROM digests WHERE org_id=?", (org_id,)
    ).fetchone()[0]


def digest_list(org_id: str, chat_id: str = "", limit: int = 30, after: str = "") -> list[dict]:
    """List digests. Wrapper matching dashboard API expectations."""
    return get_digests(org_id, chat_id=chat_id, limit=limit)


def get_chat_stats(org_id: str) -> list[dict]:
    """Overview stats for all chats in an org."""
    rows = _conn().execute(
        """SELECT chat_id,
                  COUNT(*) as message_count,
                  MAX(created_at) as last_message,
                  COUNT(DISTINCT sender_id) as unique_senders
           FROM messages WHERE org_id=?
           GROUP BY chat_id ORDER BY last_message DESC""",
        (org_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════
#  Digests
# ══════════════════════════════════════════════

def save_digest(
    org_id: str, chat_id: str, chat_name: str, date: str,
    digest_dict: dict, message_count: int = 0, image_count: int = 0,
) -> None:
    """Save or replace a daily digest for a chat."""
    c = _conn()
    digest_id = uuid.uuid4().hex[:12]
    c.execute(
        """INSERT OR REPLACE INTO digests
           (id, org_id, chat_id, chat_name, date, digest_json, message_count, image_count, created_at)
           VALUES (
             COALESCE((SELECT id FROM digests WHERE org_id=? AND chat_id=? AND date=?), ?),
             ?,?,?,?,?,?,?,?)""",
        (org_id, chat_id, date, digest_id,
         org_id, chat_id, chat_name, date,
         json.dumps(digest_dict, ensure_ascii=False),
         message_count, image_count, _now()),
    )
    c.commit()


def get_digests(org_id: str, chat_id: str = "", limit: int = 30) -> list[dict]:
    """Get digests for an org, optionally filtered by chat_id."""
    c = _conn()
    if chat_id:
        rows = c.execute(
            "SELECT * FROM digests WHERE org_id=? AND chat_id=? ORDER BY date DESC LIMIT ?",
            (org_id, chat_id, limit),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM digests WHERE org_id=? ORDER BY date DESC LIMIT ?",
            (org_id, limit),
        ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["digest_json"] = json.loads(d["digest_json"])
        except (json.JSONDecodeError, TypeError):
            pass
        results.append(d)
    return results


def get_digest_chats(org_id: str) -> list[dict]:
    """List chats that have digests, with counts."""
    rows = _conn().execute(
        """SELECT chat_id, chat_name, COUNT(*) as digest_count,
                  MIN(date) as first_date, MAX(date) as last_date
           FROM digests WHERE org_id=?
           GROUP BY chat_id ORDER BY last_date DESC""",
        (org_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════
#  AI conversations (Q&A)
# ══════════════════════════════════════════════

def create_ai_conversation(
    org_id: str, chat_id: str = "", user_id: str = "", title: str = "",
) -> str:
    """Create a new AI conversation. Returns conversation ID."""
    conv_id = uuid.uuid4().hex[:12]
    now = _now()
    c = _conn()
    c.execute(
        "INSERT INTO ai_conversations (id, org_id, chat_id, user_id, title, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (conv_id, org_id, chat_id, user_id, title, now, now),
    )
    c.commit()
    return conv_id


def get_ai_conversations(org_id: str, limit: int = 20) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM ai_conversations WHERE org_id=? ORDER BY updated_at DESC LIMIT ?",
        (org_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def save_ai_message(conversation_id: str, role: str, content: str) -> None:
    msg_id = uuid.uuid4().hex[:12]
    c = _conn()
    now = _now()
    c.execute(
        "INSERT INTO ai_messages (id, conversation_id, role, content, created_at) VALUES (?,?,?,?,?)",
        (msg_id, conversation_id, role, content, now),
    )
    c.execute("UPDATE ai_conversations SET updated_at=? WHERE id=?", (now, conversation_id))
    c.commit()


def get_ai_messages(conversation_id: str) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM ai_messages WHERE conversation_id=? ORDER BY created_at",
        (conversation_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════
#  Usage metrics
# ══════════════════════════════════════════════

def usage_record(org_id: str, metric_type: str, user_id: str = "", count: int = 1) -> None:
    """Record a usage event (message, digest, qa, summary, vision, etc.)."""
    c = _conn()
    c.execute(
        "INSERT INTO usage_metrics (org_id, user_id, metric_type, count, date, created_at) VALUES (?,?,?,?,?,?)",
        (org_id, user_id, metric_type, count, _today(), _now()),
    )
    c.commit()


def usage_get(org_id: str, date_from: str = "", date_to: str = "") -> list[dict]:
    """Get raw usage metrics for an org, optionally filtered by date range."""
    c = _conn()
    if date_from and date_to:
        rows = c.execute(
            "SELECT * FROM usage_metrics WHERE org_id=? AND date>=? AND date<=? ORDER BY date DESC, created_at DESC",
            (org_id, date_from, date_to),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM usage_metrics WHERE org_id=? ORDER BY date DESC, created_at DESC LIMIT 500",
            (org_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def usage_get_summary(org_id: str, month: str = "") -> dict:
    """Aggregate usage for a month (YYYY-MM). Returns {metric: total}."""
    if not month:
        month = _month()
    date_from = f"{month}-01"
    date_to = f"{month}-31"
    rows = _conn().execute(
        "SELECT metric_type, SUM(count) as total FROM usage_metrics "
        "WHERE org_id=? AND date>=? AND date<=? GROUP BY metric_type",
        (org_id, date_from, date_to),
    ).fetchall()
    result: dict[str, int] = {"message": 0, "digest": 0, "qa": 0, "summary": 0, "vision": 0}
    for r in rows:
        result[r["metric_type"]] = r["total"]
    return result


# ══════════════════════════════════════════════
#  Plans
# ══════════════════════════════════════════════

def plan_seed() -> None:
    """Insert the four default plans if they don't exist."""
    c = _conn()
    now = _now()
    plans = [
        ("free",       "Free",       0,     1,   3,   5,     500,    '["digest","qa"]'),
        ("basic",      "Basic",      2990,  5,   10,  50,    10000,  '["digest","qa","summary","vision"]'),
        ("standard",   "Standard",   7990,  10,  30,  999,   50000,  '["digest","qa","summary","vision","templates","scheduled"]'),
        ("enterprise", "Enterprise", 19990, 999, 999, 999,   999999, '["digest","qa","summary","vision","templates","scheduled","api","webhook","custom"]'),
    ]
    for p in plans:
        c.execute(
            "INSERT OR IGNORE INTO plans (id, name, price_thb, max_groups, max_users, max_digests_per_day, max_messages_per_month, features_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (*p, now),
        )
    c.commit()


def plan_list() -> list[dict]:
    rows = _conn().execute("SELECT * FROM plans ORDER BY price_thb").fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════
#  API tokens (per-org)
# ══════════════════════════════════════════════

def token_create(org_id: str, name: str = "", expires_days: int = 90) -> dict:
    """Create an API token scoped to an organization."""
    c = _conn()
    token_id = secrets.token_hex(4)
    token_value = f"silo_{secrets.token_hex(24)}"
    now = _now()
    expires_at = now + (expires_days * 86400) if expires_days > 0 else 0
    c.execute(
        "INSERT INTO api_tokens (id, org_id, token, name, created_at, expires_at) VALUES (?,?,?,?,?,?)",
        (token_id, org_id, token_value, name or f"token-{token_id}", now, expires_at),
    )
    c.commit()
    return {"id": token_id, "org_id": org_id, "token": token_value,
            "name": name or f"token-{token_id}", "created_at": now, "expires_at": expires_at}


def token_verify(token: str) -> str | None:
    """Verify an API token. Returns org_id if valid, None otherwise."""
    c = _conn()
    row = c.execute("SELECT * FROM api_tokens WHERE token=?", (token,)).fetchone()
    if not row:
        return None
    if row["expires_at"] and _now() > row["expires_at"]:
        c.execute("DELETE FROM api_tokens WHERE token=?", (token,))
        c.commit()
        return None
    return row["org_id"]
