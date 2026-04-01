# Silo

**AI-powered LINE chat intelligence for businesses**

Silo breaks information silos by capturing LINE group conversations and transforming them into actionable AI-powered digests, searchable knowledge bases, and real-time analytics.

## Features

- **AI Digest** -- Automatic daily/weekly summaries of group conversations
- **Vision OCR** -- Extract text from images, receipts, and documents shared in chat
- **Smart Q&A** -- Ask questions about past conversations in natural language
- **LINE Push** -- Deliver digests and alerts directly to LINE
- **Smart Templates** -- Customizable digest formats per industry
- **Real-time Alerts** -- Keyword monitoring and escalation triggers
- **Analytics Dashboard** -- Message volume, sentiment trends, response times
- **REST API** -- Full API access for integrations and automation

## Quick Start

```bash
# Clone
git clone https://github.com/nirasnet/silo.git
cd silo

# Configure
cp .env.example .env
# Edit .env with your API keys

# Install
pip install -r requirements.txt

# Run
python3 run.py
# Open http://localhost:8200
```

## Architecture

```
silo/
  app/
    main.py          -- FastAPI app factory
    config.py        -- Pydantic settings
    db.py            -- SQLite database layer
    ai/
      provider.py    -- AI engine (Claude / Gemini)
    line_oa/
      webhook.py     -- LINE webhook receiver
      api.py         -- LINE Messaging API client
    api/
      auth.py        -- Authentication
      orgs.py        -- Organization management
      digests.py     -- Digest generation & Q&A
      dashboard.py   -- Dashboard data API
    static/
      landing.html   -- Landing page
    templates/       -- HTML templates
  deploy/
    nginx.conf       -- Nginx reverse proxy config
  run.py             -- Entry point
  Dockerfile         -- Container build
```

## Pricing

| Plan | Price | Groups | Digests | Features |
|------|-------|--------|---------|----------|
| Free | 0 baht | 1 | 5/day | Basic AI digest |
| Standard | 2,990 baht/mo | 5 | Unlimited | Vision, LINE push, templates |
| Enterprise | 7,990 baht/mo | Unlimited | Unlimited | API, webhooks, priority support |

All plans include a 14-day free trial. No credit card required.

## API

Full REST API documentation available at `/docs` (Swagger UI) when the server is running.

## Tech Stack

- **Backend**: Python 3.12, FastAPI, uvicorn
- **Database**: SQLite
- **AI**: Claude Haiku 4.5 (primary), Gemini (fallback)
- **Messaging**: LINE Messaging API (Official Account)
- **OCR**: EasyOCR
- **Deploy**: Docker, Nginx, Cloudflare SSL

## License

MIT
