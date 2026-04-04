# Silo — AI Chat Intelligence Platform

## Overview
Commercial SaaS platform that connects to LINE groups via Official Account Bot, captures all messages, and provides AI-powered digests, Q&A, and analytics. Target: Thai companies (factories, sales teams) who communicate via LINE groups.

GitHub: https://github.com/nirasnet/silo
Production: https://silo.m4app.online
Login: password `silo2026`, API key `silo-api-key-2026`

## Stack
- Python 3.12 + FastAPI + uvicorn
- SQLite at /app/data/silo.db (Docker volume silo_data)
- Claude Haiku 4.5 (primary AI) + Gemini 2.0 Flash (fallback)
- LINE Messaging API (Official Account bot "ลูกชิ้น")
- EasyOCR service for image text extraction (port 9091)
- VPS: 158.220.126.195, Docker, Nginx via Cloudflare

## Architecture

```
silo/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app factory, lifespan, digest scheduler
│   │                        # Scheduler: daily at digest_hour (23:00 ICT)
│   │                        # _run_all_digests() → generates + LINE pushes for all orgs/groups
│   ├── config.py            # Pydantic settings from .env
│   ├── db.py                # SQLite layer — ALL queries org_id scoped
│   │                        # Tables: organizations, org_members, org_groups,
│   │                        #   messages, digests, ai_conversations, ai_messages,
│   │                        #   usage_metrics, plans, api_tokens
│   │                        # Key: get_production_period(org_id) — custom shift times
│   │                        # Key: message_update_text(msg_id, text) — for auto-OCR
│   ├── ai/
│   │   ├── __init__.py
│   │   └── provider.py      # AI engine with fallback chain:
│   │                        #   _call_ai() → _call_claude() → _call_gemini()
│   │                        #   generate_digest() — uses DIGEST_WITH_IMAGES_PROMPT if OCR data present
│   │                        #   ask_question() — with conversation history + suggestions
│   │                        #   analyze_image() → local OCR → Claude Vision → Gemini Vision
│   │                        #   generate_line_summary() — 6 templates (normal/detailed/simple/production/meeting/sales)
│   │                        #   Gemini models: gemini-2.0-flash, gemini-2.5-flash, gemini-2.0-flash-lite
│   ├── line_oa/
│   │   ├── __init__.py
│   │   ├── webhook.py       # POST /webhook/line — receives LINE events
│   │   │                    #   HMAC-SHA256 signature validation
│   │   │                    #   Auto-registers groups on first message
│   │   │                    #   Resolves sender names via LINE Profile API (cached)
│   │   │                    #   Auto-OCR: runs OCR on IMAGE messages → saves as [รูปภาพ-OCR] prefix
│   │   └── api.py           # LINE Messaging API client
│   │                        #   send_text, send_reply, send_push (multi-tenant channel_token)
│   │                        #   get_profile, get_group_summary, get_group_member_profile
│   │                        #   get_content (download image bytes)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── auth.py          # Auth: master API keys (config) + per-org DB tokens + JWT session cookie
│   │   │                    #   POST /auth/login → set silo_session cookie → redirect /dashboard
│   │   │                    #   GET /auth/me, GET /auth/logout
│   │   │                    #   Login always resolves to "default" slug org
│   │   ├── orgs.py          # /api/v1/org/* — CRUD orgs, members, groups, plans, usage
│   │   │                    #   Supports production_start_time in org update
│   │   ├── digests.py       # /api/v1/digest/*
│   │   │                    #   POST /generate — uses production period, auto-pushes LINE summary
│   │   │                    #   POST /generate/all — all active groups
│   │   │                    #   POST /push-summary — manual LINE push with level selection
│   │   │                    #   POST /ask — AI Q&A with conversation history
│   │   │                    #   GET /list, /chats, /production-period
│   │   └── dashboard.py     # /api/v1/dashboard/*
│   │                        #   GET /overview — stats using production period
│   │                        #   GET /groups — all groups with stats (production period scoped)
│   │                        #   GET /groups/{id}/messages, /groups/{id}/digests
│   │                        #   GET /activity, /discovered-groups
│   │                        #   GET /image/{msg_id} — proxy LINE image
│   │                        #   POST /image/{msg_id}/ocr — run OCR on image
│   │                        #   POST /groups/{id}/enable, /groups/{id}/disable
│   ├── templates/
│   │   ├── login.html       # Login page (dark theme, password auth)
│   │   └── dashboard.html   # Single-page dashboard (1155 lines, "Silo Pro" theme)
│   │                        #   Views: Dashboard, Groups, Digests, AI Chat, Settings
│   │                        #   Groups separated: LINE groups vs DMs
│   │                        #   Images: thumbnails + OCR button
│   │                        #   AI Chat: 6 suggestion buttons
│   │                        #   Click handlers: data-chatid + addEventListener (NOT inline onclick)
│   │                        #   IIFE-wrapped, brackets verified balanced
│   └── static/
│       └── landing.html     # Marketing page (1038 lines, 11 sections)
│                            #   Thai primary + English labels
│                            #   Hero, Social Proof, Problem, Features (bento grid),
│                            #   Use Cases (tabbed), How It Works, Pricing, Testimonials, FAQ, CTA, Footer
├── ocr-service/
│   ├── Dockerfile           # Python 3.12-slim + easyocr + flask
│   └── app.py               # EasyOCR Thai+English, port 9091
│                            #   /health, /api/ocr (multipart), /api/ocr/base64 (JSON)
│                            #   Auto-resizes images > 1600px for CPU performance
│                            #   Runs threaded Flask
├── run.py                   # Entry point: uvicorn on 0.0.0.0:8200
├── Dockerfile               # Python 3.12-slim, port 8200
├── requirements.txt         # fastapi, uvicorn, pydantic-settings, requests, pyjwt
├── deploy/nginx.conf        # Nginx for silo.m4app.online
├── .env                     # Secrets (gitignored)
└── .env.example             # Template
```

## Deployment

Production: /root/g-connect/silo/ on VPS 158.220.126.195
Local dev: /home/dx/m4ck.online/silo/

### Deploy all files
```bash
echo 'chskrq3@1' | sudo -S bash -c '
cp -r /home/dx/m4ck.online/silo/app /root/g-connect/silo/
cp /home/dx/m4ck.online/silo/run.py /root/g-connect/silo/
cd /root/g-connect && docker compose build --no-cache silo && docker compose up -d silo'
```

### Deploy single file (faster)
```bash
echo 'chskrq3@1' | sudo -S bash -c '
cp /home/dx/m4ck.online/silo/app/FILE_PATH /root/g-connect/silo/app/FILE_PATH
cd /root/g-connect && docker compose build --no-cache silo && docker compose up -d silo'
```

### Check logs
```bash
echo 'chskrq3@1' | sudo -S bash -c 'cd /root/g-connect && docker compose logs silo --tail 20'
```

### Port 8200 conflict fix
```bash
echo 'chskrq3@1' | sudo -S bash -c 'cd /root/g-connect && docker compose stop silo && fuser -k 8200/tcp 2>/dev/null && sleep 1 && docker compose start silo'
```

### IMPORTANT: Files are COPY'd at Docker build time. Must `docker compose build --no-cache` then `up -d`.

## LINE Official Account
- Bot name: ลูกชิ้น (TODO: rename to "Silo AI")
- Channel Secret: fb8b174e9f1c3dcddb90a41f91ccf976
- Channel Token: in .env (LINE_CHANNEL_TOKEN)
- Webhook URL: https://silo.m4app.online/webhook/line
- Bot auto-registers groups when receiving first message (no manual MID)
- Webhook validates HMAC-SHA256 signature with channel secret

## Key Features

### Production Date (Factory Shift Support)
- Org setting: production_start_time (default "00:00")
- Example: "07:25" means production day = 07:25 to D+1 07:25
- If current time < start_time → production date = yesterday
- All dashboard stats + digests use production period
- API: GET /api/v1/digest/production-period
- Settings UI: time picker with live preview

### Auto-OCR Pipeline
- Webhook receives IMAGE message → auto-downloads from LINE → runs OCR
- OCR chain: local EasyOCR (172.18.0.1:9091) → Claude Vision → Gemini Vision
- OCR text saved to message as "[รูปภาพ-OCR] extracted text..."
- Digest generation detects [รูปภาพ-OCR] prefix → uses DIGEST_WITH_IMAGES_PROMPT
- Enhanced prompt extracts: stop_time, problems, machine info from factory documents

### AI Pipeline
- Primary: Claude Haiku 4.5 (ANTHROPIC_API_KEY)
- Fallback: Gemini 2.0 Flash → 2.5 Flash → 2.0 Flash Lite (GEMINI_API_KEY)
- Digest: structured JSON (summary, topics, action_items, problems, stop_time, numbers, people, sentiment, image_data)
- Q&A: digests as context → answer with follow-up suggestions
- LINE Summary: 6 templates (normal, detailed, simple, production, meeting, sales)
- Auto-push LINE summary after digest generation

### LINE Push Summary
- After digest: auto-generates LINE-formatted summary → pushes to group
- POST /api/v1/digest/push-summary — manual push with level selection
- Scheduler: _run_all_digests() pushes LINE summary after each digest
- Per-org channel token, per-group summary_level

### Multi-tenant
- All data scoped by org_id
- Plans: free(0), basic(2990), standard(7990), enterprise(19990) THB/month
- Usage metering: digest, qa, summary, vision, message
- Per-org LINE OA credentials (future multi-customer support)

### Dashboard (1155 lines, "Silo Pro" theme)
- Views: Dashboard, Groups, Digests, AI Chat, Settings
- Groups: LINE groups vs DMs separated
- Group detail: tabbed Messages/Digests, image thumbnails + OCR button
- AI Chat: group selector + 6 suggestion buttons (สรุปวันนี้, ปัญหา, Action Items, คนแอคทีฟ, ตัวเลข/KPI, เทียบเมื่อวาน)
- Production date shown in sidebar + all stat cards
- Click handlers: data-chatid + addEventListener (safe, no inline onclick)
- Auto-refresh 30s, toast notifications, mobile responsive

## API Endpoints (35+)

### Auth
- POST /auth/login — password → session cookie → redirect /dashboard
- GET /auth/me → {user_id, org_id, role, org_name}
- GET /auth/logout — clear cookie

### Organizations (/api/v1/org/)
- GET /plans — 4 pricing tiers
- POST /create — {name, slug} → auto-add creator as owner
- GET /{id}, PUT /{id} — including production_start_time
- GET /{id}/members, POST /{id}/members, DELETE /{id}/members/{uid}
- GET /{id}/groups, POST /{id}/groups, DELETE /{id}/groups/{mid}
- GET /{id}/usage, GET /{id}/usage/summary

### Digests (/api/v1/digest/)
- POST /generate — {chat_id, last_24h?, date?} → AI digest + LINE push
- POST /generate/all — all active groups
- POST /push-summary — {chat_id, level?} → LINE push summary
- POST /ask — {chat_id, message, conversation_id?} → AI answer
- GET /list — digests list
- GET /chats — chats with digests
- GET /production-period — {production_date, start_str, end_str}

### Dashboard (/api/v1/dashboard/)
- GET /overview — stats (production period scoped)
- GET /groups — groups with stats
- GET /groups/{id}/messages?limit=30 — message list
- GET /groups/{id}/digests?limit=5 — digest list
- GET /activity — activity feed
- GET /discovered-groups — auto-detected groups
- POST /groups/{id}/enable, POST /groups/{id}/disable
- GET /image/{msg_id} — proxy LINE image (auth)
- POST /image/{msg_id}/ocr — run OCR on image

### Webhook
- POST /webhook/line — LINE OA events (signature validated)

## Environment Variables (.env)
```
SECRET_KEY=silo-secret-2026
WEB_PASSWORD=silo2026
API_KEYS=["silo-api-key-2026"]
DOMAIN=silo.m4app.online
ANTHROPIC_API_KEY=sk-ant-api03-...
AI_MODEL=claude-haiku-4-5-20251001
GEMINI_API_KEY=AIzaSyDwqGInViX-3wTvJp5CSpLhGqVaY5m1Uso
LINE_CHANNEL_SECRET=fb8b174e9f1c3dcddb90a41f91ccf976
LINE_CHANNEL_TOKEN=lhWJnIrznVsg5T77PnH4u2Lj7HMqswGdqwunvaItbi4l0Y3lLldSaXBAJNbd8tZadjefqayrgIGwy6K/4NiQqg8F3YT9dlCO6rn4pEEE7lgrD81x0eP9eUtwTJ1Y8y9Ik3eNLQQW0vw4Reb13vsYogdB04t89/1O/w1cDnyilFU=
OCR_URL=http://172.18.0.1:9091
```

## Docker Setup
- Container: silo-app, port 8200
- Volume: silo_data:/app/data (SQLite DB)
- Network: gconnect-network (172.18.0.0/16)
- Nginx: silo.m4app.online → silo-app:8200 (SSL via Cloudflare)
- docker-compose.yml at /root/g-connect/docker-compose.yml

## Current State (April 2026)
- 11 LINE groups connected (PCG Official 2015, PCG Safety Health, DX GW, Test123, DMs)
- 298+ messages captured via LINE OA webhook
- 272+ AI digests generated
- Auto-OCR: image messages get OCR'd automatically on receive
- LINE push: summaries auto-sent after digest generation
- Dashboard: "Silo Pro" theme, working group detail, images, AI chat
- Landing page: premium 11-section Thai+EN marketing site
- OCR service: dockerized, ready in ocr-service/

## Known Issues / Active TODO
1. **Anthropic API credits empty** — need to add credits at console.anthropic.com or switch to Gemini-only
2. **Gemini free quota** — resets daily, may hit limits with heavy use
3. **Rename LINE bot** — from "ลูกชิ้น" to "Silo AI" in LINE Official Account Manager
4. **OCR service** — currently runs on host (port 9091), needs adding to docker-compose.yml
5. **Dashboard design** — functional but needs more polish for enterprise customers
6. **Multi-org onboarding** — currently single "Default" org, need signup flow for new customers
7. **Billing** — no payment integration yet (Stripe or PromptPay QR needed)
8. **LINE token** — personal mline LINE token expired, need QR re-login for personal client

## Development History
- Started as mLINE personal project (LINE Thrift reverse-engineered client)
- Forked to Silo as commercial SaaS (LINE Official Account API)
- mLINE repo: https://github.com/nirasnet/M-Line (personal version, separate project at /home/dx/m4ck.online/mline/)
- Silo is independent — no Thrift code, uses official LINE Messaging API only
