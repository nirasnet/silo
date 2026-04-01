"""Organization management API routes — /api/v1/org/."""

import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from .auth import require_auth, require_admin

router = APIRouter(prefix="/api/v1/org")


# ── Request schemas ──

class CreateOrgRequest(BaseModel):
    name: str
    slug: str


class UpdateOrgRequest(BaseModel):
    name: Optional[str] = None
    plan: Optional[str] = None
    max_groups: Optional[int] = None
    max_users: Optional[int] = None
    logo_url: Optional[str] = None
    status: Optional[str] = None
    digest_hour: Optional[int] = None
    ai_model: Optional[str] = None


class AddMemberRequest(BaseModel):
    user_id: str
    role: str = "member"
    email: Optional[str] = None
    display_name: Optional[str] = None


class AddGroupRequest(BaseModel):
    group_mid: str
    group_name: str = ""


class LineConfigRequest(BaseModel):
    line_channel_id: Optional[str] = None
    line_channel_secret: Optional[str] = None
    line_channel_token: Optional[str] = None


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,48}[a-z0-9]$")


def _get_db(request: Request):
    return request.app.db


def _require_org(db, org_id: str) -> dict:
    org = db.org_get(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


def _strip_secrets(org: dict) -> dict:
    """Remove sensitive fields from org dict."""
    safe = dict(org)
    safe.pop("line_channel_secret", None)
    safe.pop("line_channel_token", None)
    return safe


# ── Plans (before /{org_id} to avoid route shadowing) ──

PLANS = [
    {
        "id": "free",
        "name": "Free",
        "price": 0,
        "max_groups": 1,
        "max_users": 2,
        "max_digests_day": 3,
        "max_ai_queries_day": 10,
        "features": ["1 LINE group", "Daily digest", "Basic AI Q&A"],
    },
    {
        "id": "starter",
        "name": "Starter",
        "price": 299,
        "max_groups": 5,
        "max_users": 5,
        "max_digests_day": 20,
        "max_ai_queries_day": 50,
        "features": ["5 LINE groups", "Scheduled digests", "AI Q&A", "LINE push summary"],
    },
    {
        "id": "pro",
        "name": "Pro",
        "price": 799,
        "max_groups": 20,
        "max_users": 15,
        "max_digests_day": 100,
        "max_ai_queries_day": 200,
        "features": ["20 LINE groups", "Vision/image digest", "Priority AI", "API access", "Custom branding"],
    },
    {
        "id": "enterprise",
        "name": "Enterprise",
        "price": 2499,
        "max_groups": 100,
        "max_users": 50,
        "max_digests_day": 999,
        "max_ai_queries_day": 999,
        "features": ["Unlimited groups", "Dedicated AI", "SSO", "Audit logs", "SLA support"],
    },
]


@router.get("/plans", name="list_plans")
async def list_plans(auth: dict = Depends(require_auth)):
    return PLANS


# ── Organization CRUD ──

@router.post("/create")
async def create_org(body: CreateOrgRequest, request: Request, auth: dict = Depends(require_auth)):
    db = _get_db(request)
    slug = body.slug.lower().strip()
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="Slug must be 3-50 chars, lowercase alphanumeric and hyphens")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    if db.org_get_by_slug(slug):
        raise HTTPException(status_code=409, detail="Slug already taken")

    try:
        org = db.org_create(body.name.strip(), slug)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Auto-add creator as owner
    db.org_add_member(org["id"], auth["user_id"], role="owner")
    return _strip_secrets(org)


@router.get("/{org_id}")
async def get_org(org_id: str, request: Request, auth: dict = Depends(require_auth)):
    db = _get_db(request)
    org = _require_org(db, org_id)
    return _strip_secrets(org)


@router.put("/{org_id}")
async def update_org(org_id: str, body: UpdateOrgRequest, request: Request, auth: dict = Depends(require_admin)):
    db = _get_db(request)
    _require_org(db, org_id)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    db.org_update(org_id, **updates)
    return {"ok": True}


# ── Members ──

@router.get("/{org_id}/members")
async def list_members(org_id: str, request: Request, auth: dict = Depends(require_auth)):
    db = _get_db(request)
    _require_org(db, org_id)
    return db.org_get_members(org_id)


@router.post("/{org_id}/members")
async def add_member(org_id: str, body: AddMemberRequest, request: Request, auth: dict = Depends(require_admin)):
    db = _get_db(request)
    org = _require_org(db, org_id)
    current = db.org_get_members(org_id)
    max_users = org.get("max_users", 3)
    if len(current) >= max_users:
        raise HTTPException(status_code=403, detail=f"Member limit reached ({max_users})")
    if body.role not in ("owner", "admin", "member", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role. Must be: owner, admin, member, viewer")
    try:
        member_id = db.org_add_member(
            org_id, body.user_id, role=body.role,
            email=body.email, display_name=body.display_name,
            invited_by=auth["user_id"],
        )
    except Exception:
        raise HTTPException(status_code=409, detail="User is already a member")
    return {"id": member_id, "ok": True}


@router.delete("/{org_id}/members/{target_user_id}")
async def remove_member(org_id: str, target_user_id: str, request: Request, auth: dict = Depends(require_admin)):
    db = _get_db(request)
    _require_org(db, org_id)
    if target_user_id == auth["user_id"]:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")
    db.org_remove_member(org_id, target_user_id)
    return {"ok": True}


# ── Groups ──

@router.get("/{org_id}/groups")
async def list_groups(org_id: str, request: Request, auth: dict = Depends(require_auth)):
    db = _get_db(request)
    _require_org(db, org_id)
    return db.org_get_groups(org_id)


@router.post("/{org_id}/groups")
async def add_group(org_id: str, body: AddGroupRequest, request: Request, auth: dict = Depends(require_admin)):
    db = _get_db(request)
    org = _require_org(db, org_id)
    current = db.org_get_groups(org_id)
    max_groups = org.get("max_groups", 1)
    if len(current) >= max_groups:
        raise HTTPException(status_code=403, detail=f"Group limit reached ({max_groups})")
    try:
        group_id = db.org_add_group(org_id, body.group_mid, group_name=body.group_name)
    except Exception:
        raise HTTPException(status_code=409, detail="Group is already connected")
    return {"id": group_id, "ok": True}


@router.delete("/{org_id}/groups/{group_mid}")
async def remove_group(org_id: str, group_mid: str, request: Request, auth: dict = Depends(require_admin)):
    db = _get_db(request)
    _require_org(db, org_id)
    db.org_remove_group(org_id, group_mid)
    return {"ok": True}


# ── Usage ──

@router.get("/{org_id}/usage")
async def get_usage(org_id: str, request: Request, month: str = Query(default=""), auth: dict = Depends(require_auth)):
    db = _get_db(request)
    _require_org(db, org_id)
    if month:
        return db.usage_get(org_id, date_from=f"{month}-01", date_to=f"{month}-31")
    return db.usage_get(org_id)


@router.get("/{org_id}/usage/summary")
async def get_usage_summary(org_id: str, request: Request, month: str = Query(default=""), auth: dict = Depends(require_auth)):
    db = _get_db(request)
    _require_org(db, org_id)
    return db.usage_get_summary(org_id, month=month)


# ── LINE OA Config ──

@router.put("/{org_id}/line-config")
async def update_line_config(org_id: str, body: LineConfigRequest, request: Request, auth: dict = Depends(require_admin)):
    db = _get_db(request)
    _require_org(db, org_id)
    updates = {}
    if body.line_channel_id is not None:
        updates["line_channel_id"] = body.line_channel_id
    if body.line_channel_secret is not None:
        updates["line_channel_secret"] = body.line_channel_secret
    if body.line_channel_token is not None:
        updates["line_channel_token"] = body.line_channel_token
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    db.org_update(org_id, **updates)
    return {"ok": True}
