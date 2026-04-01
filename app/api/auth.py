"""Silo authentication — API key, session cookie (JWT), login/logout."""

import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Cookie
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from pathlib import Path

from ..config import settings

router = APIRouter()


# ── JWT helpers (HMAC-SHA256, compact) ──

def _b64e(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return urlsafe_b64decode(s)


def _jwt_sign(payload: dict) -> str:
    header = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64e(json.dumps(payload).encode())
    msg = f"{header}.{body}"
    sig = hmac.new(settings.secret_key.encode(), msg.encode(), hashlib.sha256).digest()
    return f"{msg}.{_b64e(sig)}"


def _jwt_verify(token: str) -> Optional[dict]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        msg = f"{parts[0]}.{parts[1]}"
        expected = hmac.new(settings.secret_key.encode(), msg.encode(), hashlib.sha256).digest()
        actual = _b64d(parts[2])
        if not hmac.compare_digest(expected, actual):
            return None
        payload = json.loads(_b64d(parts[1]))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def _create_session(org_id: str, user_id: str, role: str) -> str:
    payload = {
        "org_id": org_id,
        "user_id": user_id,
        "role": role,
        "exp": int(time.time()) + 86400 * 7,  # 7 days
        "iat": int(time.time()),
    }
    return _jwt_sign(payload)


# ── Dependencies ──

async def require_auth(request: Request) -> dict:
    """Authenticate via API key, Bearer token, or session cookie.
    Returns {"org_id": str, "user_id": str, "role": str}.
    """
    # 1. Check X-API-Key header or ?api_key query param
    api_key = request.headers.get("x-api-key") or request.query_params.get("api_key")
    if api_key:
        # Check config API keys first (master keys)
        from app.config import settings
        if api_key in settings.api_keys:
            # Resolve actual default org id (slug "default")
            _db = request.app.db
            _default_org = _db.org_get_by_slug("default")
            _org_id = _default_org["id"] if _default_org else "default"
            return {"org_id": _org_id, "user_id": "admin", "role": "owner"}
        # Check DB tokens (per-org keys)
        db = request.app.db
        org_id = db.token_verify(api_key)
        if org_id:
            return {"org_id": org_id, "user_id": "api", "role": "member"}
        raise HTTPException(status_code=401, detail="Invalid API key")

    # 2. Check Authorization: Bearer <token>
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        payload = _jwt_verify(auth_header[7:])
        if payload:
            return {
                "org_id": payload.get("org_id", ""),
                "user_id": payload.get("user_id", ""),
                "role": payload.get("role", "member"),
            }
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # 3. Check session cookie
    session = request.cookies.get("silo_session")
    if session:
        payload = _jwt_verify(session)
        if payload:
            return {
                "org_id": payload.get("org_id", ""),
                "user_id": payload.get("user_id", ""),
                "role": payload.get("role", "member"),
            }

    raise HTTPException(status_code=403, detail="Authentication required")


async def require_admin(auth: dict = Depends(require_auth)) -> dict:
    """Require owner or admin role."""
    if auth["role"] not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return auth


# ── Login request ──

class LoginRequest(BaseModel):
    password: str
    org_slug: Optional[str] = None


# ── Routes ──

@router.get("/auth/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render the login page, or redirect to dashboard if already logged in."""
    session = request.cookies.get("silo_session")
    if session and _jwt_verify(session):
        return RedirectResponse(url="/dashboard", status_code=302)
    html_path = Path(__file__).parent.parent / "templates" / "login.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>Silo Login</h1><p>Template not found</p>", status_code=500)


@router.post("/auth/login")
async def login_submit(request: Request):
    """Login via form POST (web) or JSON body. Sets session cookie."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        body = await request.json()
        password = body.get("password", "")
        org_slug = body.get("org_slug", "")
    else:
        form = await request.form()
        password = form.get("password", "")
        org_slug = form.get("org_slug", "")

    if password != settings.web_password:
        if "application/json" in content_type:
            raise HTTPException(status_code=401, detail="Invalid password")
        # Re-render login page with error
        html_path = Path(__file__).parent.parent / "templates" / "login.html"
        html = html_path.read_text() if html_path.exists() else "<h1>Error</h1>"
        html = html.replace("<!--ERROR-->", '<div class="error">รหัสผ่านไม่ถูกต้อง</div>')
        return HTMLResponse(html, status_code=401)

    # Resolve org — use slug or default to first org
    db = request.app.db
    org = None
    if org_slug:
        org = db.org_get_by_slug(org_slug)
    if not org:
        orgs = db.org_list()
        if orgs:
            org = orgs[0]
        else:
            # Auto-create default org on first login
            org = db.org_create("Default", "default")
            db.org_add_member(org["id"], "admin", role="owner")

    org_id = org["id"]
    user_id = "admin"
    role = "owner"

    # Check member role
    members = db.org_get_members(org_id)
    for m in members:
        if m.get("user_id") == user_id:
            role = m.get("role", "member")
            break

    token = _create_session(org_id, user_id, role)

    if "application/json" in content_type:
        resp = JSONResponse({"ok": True, "token": token, "org_id": org_id})
    else:
        resp = RedirectResponse(url="/dashboard", status_code=302)

    resp.set_cookie(
        key="silo_session", value=token,
        httponly=True, samesite="lax",
        max_age=86400 * 7,
    )
    return resp


@router.post("/auth/logout")
async def logout():
    """Clear session cookie."""
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("silo_session")
    return resp


@router.get("/auth/logout")
async def logout_redirect():
    """Clear session cookie and redirect to login."""
    resp = RedirectResponse(url="/auth/login", status_code=302)
    resp.delete_cookie("silo_session")
    return resp


@router.get("/auth/me")
async def get_me(request: Request, auth: dict = Depends(require_auth)):
    """Return current user info."""
    db = request.app.db
    org = db.org_get(auth["org_id"])
    return {
        "user_id": auth["user_id"],
        "org_id": auth["org_id"],
        "role": auth["role"],
        "org_name": org["name"] if org else "",
        "org_slug": org.get("slug", "") if org else "",
    }
