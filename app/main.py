"""Silo — FastAPI application factory."""

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


def create_app() -> FastAPI:
    from . import db
    from .config import settings

    db.init_db()
    db.plan_seed()
    print("[Silo] DB initialized, plans seeded")

    @asynccontextmanager
    async def lifespan(the_app: FastAPI):
        # Start digest scheduler
        t = threading.Thread(target=_digest_scheduler, daemon=True)
        t.start()
        print("[Silo] Digest scheduler started")
        yield

    app = FastAPI(
        title="Silo",
        description="AI-powered LINE chat intelligence for businesses",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    from .line_oa.webhook import router as webhook_router
    from .api.auth import router as auth_router
    from .api.orgs import router as orgs_router
    from .api.digests import router as digests_router
    from .api.dashboard import router as dashboard_router

    app.include_router(webhook_router)
    app.include_router(auth_router)
    app.include_router(orgs_router)
    app.include_router(digests_router)
    app.include_router(dashboard_router)

    # Attach modules for request.app.db / request.app.ai access
    app.db = db
    from .ai import provider as ai_provider
    app.ai = ai_provider

    # Static files
    static_dir = Path(__file__).parent / "static"
    templates_dir = Path(__file__).parent / "templates"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "silo"}

    @app.get("/")
    async def landing():
        return FileResponse(str(static_dir / "landing.html"))

    @app.get("/login")
    async def login_page(request: Request):
        from .api.auth import _jwt_verify
        session = request.cookies.get("silo_session")
        if session and _jwt_verify(session):
            return RedirectResponse(url="/dashboard", status_code=302)
        html = (templates_dir / "login.html").read_text()
        return HTMLResponse(html)

    @app.get("/dashboard")
    async def dashboard_page(request: Request):
        from .api.auth import _jwt_verify
        session = request.cookies.get("silo_session")
        if not session or not _jwt_verify(session):
            return RedirectResponse(url="/login", status_code=302)
        html = (templates_dir / "dashboard.html").read_text()
        return HTMLResponse(html)

    return app


def _digest_scheduler():
    """Background thread: run daily digests for all orgs at configured hour."""
    import time
    from datetime import datetime, timezone, timedelta

    TH_TZ = timezone(timedelta(hours=7))
    from .config import settings

    last_run_date = ""
    while True:
        try:
            now = datetime.now(TH_TZ)
            today = now.strftime("%Y-%m-%d")
            if now.hour == settings.digest_hour and today != last_run_date:
                last_run_date = today
                print(f"[Silo-Digest] Running daily digest for {today}")
                _run_all_digests(today)
        except Exception as e:
            print(f"[Silo-Digest] Error: {e}")
        time.sleep(60)


def _run_all_digests(date_str: str):
    """Generate digests for all active orgs and groups."""
    from . import db
    from .ai.provider import generate_digest

    orgs = db.org_list()
    for org in orgs:
        if org.get("status") != "active":
            continue
        org_id = org["id"]
        groups = db.org_get_groups(org_id)
        for group in groups:
            if not group.get("auto_digest", 1):
                continue
            chat_id = group["group_mid"]
            chat_name = group.get("group_name", chat_id[:16])
            try:
                messages = db.get_messages(org_id, chat_id, limit=5000)
                if len(messages) < 2:
                    continue
                digest = generate_digest(chat_name, date_str, messages)
                if digest:
                    db.save_digest(org_id, chat_id, chat_name, date_str, digest, len(messages))
                    db.usage_record(org_id, "digest")
                    print(f"[Silo-Digest] {org['name']}/{chat_name}: {len(messages)} msgs → digest OK")
            except Exception as e:
                print(f"[Silo-Digest] Error {chat_name}: {e}")
