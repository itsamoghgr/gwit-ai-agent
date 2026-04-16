# GW IT Support Dashboard

## About

The GW IT Support Dashboard is an internal tool that helps the GW IT team make sense of large volumes of support tickets (incidents and work orders).

Instead of reading tickets one by one, the dashboard groups similar tickets together into **clusters** — each cluster represents a recurring type of issue (for example, "VPN connection failures" or "Outlook password resets"). For every cluster, the dashboard shows:

- **How big the problem is** — how many tickets fall into it, which services are affected, and where the tickets came from
- **What the issue looks like** — a short AI-generated summary of the cluster and the individual tickets inside it
- **Whether the knowledge base covers it** — existing KB articles that match the cluster, and gaps where new articles are needed
- **Suggested KB articles** — drafts generated for clusters that don't have good documentation yet

The dashboard is organized around **pipeline runs**. Each run is a snapshot of the ticket data at a point in time, with its own clustering results and KB suggestions, so users can compare how issues evolve over time.

Finally, an **AI chat assistant** lets users ask plain-English questions about the data and the knowledge base ("what are the top issues this month?", "is there an article about MFA setup?") and get grounded answers.

In short: it turns a flood of individual tickets into a clear picture of what's breaking, how often, and what documentation is missing.

## Structure

```
app/
├── backend/   FastAPI API (runs, clusters, kb, chat)
└── frontend/  Next.js 16 + React 19 + Tailwind 4
```

## Prerequisites

- Python 3.11+
- Node.js 20+
- PostgreSQL 15+ with the `pgvector` extension
- Azure OpenAI access (endpoint + API key)

## Setup

### 1. Environment variables

Create `app_gw-it/.env` (sibling to this `app/` directory) with:

```
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=gw_it_ticket_db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password

AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=your_key
AZURE_OPENAI_API_VERSION=2025-01-01-preview
AZURE_OPENAI_EMBED_MODEL=text-embedding-ada-002
AZURE_OPENAI_DEPLOYMENT=gpt-4.1
```

### 2. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

API runs at `http://localhost:8000`. Health check: `GET /api/health`.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

App runs at `http://localhost:3000` and talks to the backend at `:8000` (CORS is preconfigured).

## Build

```bash
cd frontend && npm run build && npm start
```
