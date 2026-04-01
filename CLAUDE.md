# Silo -- AI Chat Intelligence Platform

## Overview
Commercial SaaS platform that connects to LINE groups via Official Account Bot, captures all messages, and provides AI-powered digests, Q&A, and analytics.

## Stack
- Python 3.12 + FastAPI + uvicorn
- SQLite (data/silo.db)
- Claude Haiku 4.5 (primary AI) + Gemini (fallback)
- LINE Messaging API (Official Account)
- Local EasyOCR for image text extraction

## Architecture
- app/main.py -- FastAPI app factory
- app/config.py -- Pydantic settings
- app/db.py -- SQLite database layer (all queries org_id scoped)
- app/ai/provider.py -- AI engine (Claude/Gemini)
- app/line_oa/webhook.py -- LINE webhook receiver
- app/line_oa/api.py -- LINE Messaging API client
- app/api/auth.py -- Authentication (API key + session)
- app/api/orgs.py -- Organization management
- app/api/digests.py -- AI digest generation & Q&A
- app/api/dashboard.py -- Dashboard data API
- app/templates/ -- HTML templates (login, dashboard)
- app/static/ -- Static files (landing page)

## Deployment
- Container: silo-app, port 8200
- Domain: silo.m4app.online
- Docker volume: silo_data:/app/data

## Commands
```bash
python3 run.py  # Start server on port 8200
```
