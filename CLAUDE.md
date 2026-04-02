# Silo — AI Chat Intelligence Platform

## Overview
Commercial SaaS platform that connects to LINE groups via Official Account Bot, captures all messages, and provides AI-powered digests, Q&A, and analytics. Target: Thai companies (factories, sales teams) who communicate via LINE groups.

GitHub: https://github.com/nirasnet/silo
Production: https://silo.m4app.online
Login: password `silo2026`, API key `silo-api-key-2026`

## Stack
- Python 3.12 + FastAPI + uvicorn
- SQLite at /app/data/silo.db (Docker volume silo_data)
- Claude Haiku 4.5 (primary AI) + Gemini (fallback)
- LINE Messaging API (Official Account bot "ลูกชิ้น")
- Local EasyOCR at http://172.18.0.1:9091 for image text extraction

## Architecture

```
silo/
├── app/
│   ├── main.py              # FastAPI app factory, lifespan, digest scheduler
│   ├── config.py             # Pydantic settings from .env
│   ├── db.py                 # SQLite layer — ALL queries org_id scoped
│   │                         # Tables: organizations, org_members, org_groups,
│   │                         #   messages, digests, ai_conversations, ai_messages,
│   │                         #   usage_metrics, plans, api_tokens
│   │                         # Production date utility: get_production_period()
│   ├── ai/
│   │   └── provider.py       # _call_ai (Claude→Gemini), generate_digest,
│   │                         #   ask_question, analyze_image (OCR→Claude→Gemini),
│   │                         #   generate_line_summary (6 templates)
│   ├── line_oa/
│   │   ├── webhook.py        # POST /webhook/line — receives LINE events,
│   │   │                     #   validates HMAC signature, auto-registers groups,
│   │   │                     #   resolves sender names via LINE API
│   │   └── api.py            # LINE Messaging API client (send, profile, content)
│   ├── api/
│   │   ├── auth.py           # Auth: API key (config + DB tokens) + JWT session cookie
│   │   │                     #   POST /auth/login, GET /auth/me, GET /auth/logout
│   │   ├── orgs.py           # /api/v1/org/* — CRUD orgs, members, groups, plans, usage
│   │   ├── digests.py        # /api/v1/digest/* — generate, list, ask AI, production-period
│   │   └── dashboard.py      # /api/v1/dashboard/* — overview, groups, messages,
│   │                         #   activity, discovered-groups, image proxy, OCR
│   ├── templates/
│   │   ├── login.html        # Login page (password auth)
│   │   └── dashboard.html    # Single-page dashboard app (vanilla JS)
│   └── static/
│       └── landing.html      # Public marketing landing page (Thai-first)
├── run.py                    # Entry point: uvicorn on 0.0.0.0:8200
├── Dockerfile                # Python 3.12-slim, port 8200
├── requirements.txt          # fastapi, uvicorn, pydantic-settings, requests, pyjwt
├── deploy/nginx.conf         # Nginx reverse proxy for silo.m4app.online
├── .env                      # API keys (gitignored)
└── .env.example              # Template
```

## Deployment

Production path: /root/g-connect/silo/ on VPS 158.220.126.195
Local dev path: /home/dx/m4ck.online/silo/

### Deploy
```bash
# Copy files to production
echo 'chskrq3@1' | sudo -S bash -c 'cp -r /home/dx/m4ck.online/silo/app /root/g-connect/silo/ && cp /home/dx/m4ck.online/silo/run.py /root/g-connect/silo/'

# Build and restart
echo 'chskrq3@1' | sudo -S bash -c 'cd /root/g-connect && docker compose build --no-cache silo && docker compose up -d silo'

# Check logs
echo 'chskrq3@1' | sudo -S bash -c 'cd /root/g-connect && docker compose logs silo --tail 20'
```

### Important: Files are COPY'd at Docker build time. Must `docker compose build --no-cache` then `up -d` to pick up changes.

## LINE Official Account
- Bot name: ลูกชิ้น (should rename to "Silo AI")
- Channel Secret: in .env (LINE_CHANNEL_SECRET)
- Channel Token: in .env (LINE_CHANNEL_TOKEN)
- Webhook URL: https://silo.m4app.online/webhook/line
- Bot auto-registers groups when it receives messages (no manual MID needed)

## Key Features

### Production Date
- Organizations can set custom production start time (e.g. 07:25)
- If current time < start time, production date = yesterday
- Range: D-1 start_time → now (or D start_time → D+1 start_time for specific dates)
- All dashboard stats and digests use production period, not calendar day
- Config in Settings → เวลาเริ่มวันผลิต
- API: GET /api/v1/digest/production-period

### AI Pipeline
- Digest: messages → Claude Haiku → structured JSON (summary, topics, action_items, problems, numbers, people, sentiment)
- Q&A: digests as context → Claude → answer with suggestions
- Image OCR: local EasyOCR (172.18.0.1:9091) → Claude Vision → Gemini Vision
- 6 summary templates: normal, detailed, simple, production, meeting, sales

### Multi-tenant
- All data scoped by org_id
- Organizations with plans (free/basic/standard/enterprise)
- Usage metering per org (digest, qa, summary, vision, message)
- Per-org LINE OA credentials (future: each customer uses their own bot)

## API Endpoints (30+)

### Auth
- POST /auth/login — password + optional org_slug → session cookie
- GET /auth/me — current user info
- GET /auth/logout — clear session

### Organizations (/api/v1/org/)
- GET /plans, POST /create, GET /{id}, PUT /{id}
- GET /{id}/members, POST /{id}/members, DELETE /{id}/members/{uid}
- GET /{id}/groups, POST /{id}/groups, DELETE /{id}/groups/{mid}
- GET /{id}/usage, GET /{id}/usage/summary

### Digests (/api/v1/digest/)
- POST /generate — {chat_id, last_24h?, date?} → AI digest (uses production period)
- POST /generate/all — all active groups
- GET /list — list digests
- GET /chats — chats with digests
- POST /ask — {chat_id, message} → AI answer
- GET /production-period — current production date/range

### Dashboard (/api/v1/dashboard/)
- GET /overview — stats using production period
- GET /groups — all groups with stats
- GET /groups/{id}/messages — message list
- GET /groups/{id}/digests — digest list
- GET /activity — activity feed
- GET /discovered-groups — auto-detected groups
- POST /groups/{id}/enable, POST /groups/{id}/disable
- GET /image/{msg_id} — proxy LINE image (auth required)
- POST /image/{msg_id}/ocr — run OCR on image

### Webhook
- POST /webhook/line — LINE Official Account events

## Environment Variables (.env)
```
SECRET_KEY=silo-secret-2026
WEB_PASSWORD=silo2026
API_KEYS=["silo-api-key-2026"]
DOMAIN=silo.m4app.online
ANTHROPIC_API_KEY=sk-ant-...
AI_MODEL=claude-haiku-4-5-20251001
GEMINI_API_KEY=AIza...
LINE_CHANNEL_SECRET=fb8b...
LINE_CHANNEL_TOKEN=lhWJ...
OCR_URL=http://172.18.0.1:9091
```

## Docker Network Note
If Docker containers lose internet after reboot/restart:
```bash
echo 'chskrq3@1' | sudo -S bash -c 'cd /root/g-connect && docker compose stop silo && fuser -k 8200/tcp 2>/dev/null && sleep 1 && docker compose start silo'
```

## Current State (April 2026)
- 10 LINE groups connected (PCG Official 2015, PCG Safety Health, DX GW, Test123, etc.)
- 165+ messages captured via LINE OA webhook
- 40+ AI digests generated
- Dashboard: working with group/DM separation, image display, OCR, AI chat with suggestions
- Production date config: working (configurable per org)
- Landing page: live at silo.m4app.online

## Known Issues / TODO
- Dashboard UI needs professional design polish
- Need to rename LINE bot from "ลูกชิ้น" to "Silo AI"
- Need real pilot customers (3 target companies)
- OCR service (port 9091) must be running on host for image analysis
- LINE token refresh not automated (manual re-login if expired)
