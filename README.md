<!-- # Project documentation
# Contains: architecture diagram, tech stack table, quick start guide,
# API endpoint table, project structure, multi-tenancy explanation -->

# Voice Agent Platform

Production-ready, multi-tenant AI Voice Calling Agent SaaS platform.

## Architecture

```
User Voice → Twilio PSTN → Media Streams WebSocket → Deepgram STT (streaming)
    → OpenAI GPT-4.1 mini (streaming) → Cartesia Sonic 3 TTS (streaming)
    → Media Streams WebSocket → Twilio PSTN → User
```

All three AI stages stream concurrently — LLM starts generating as soon as STT
produces a final transcript, and TTS starts synthesizing as soon as the first LLM
token arrives. Target latency: **<500ms** perceived response time.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, FastAPI, Uvicorn |
| Database | PostgreSQL 16 + TimescaleDB |
| Cache/Queue | Redis 7 + Celery |
| Telephony | Twilio (global) + Plivo (India) |
| STT | Deepgram Nova-3 (Hindi-English code-switching) |
| LLM | OpenAI GPT-4.1 mini + Anthropic Claude (fallback) |
| TTS | Cartesia Sonic 3 + ElevenLabs (fallback) |
| Storage | MinIO (self-hosted) / AWS S3 |
| Monitoring | Prometheus + Grafana + Loki |

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env with your API keys

# 2. Start infrastructure
docker compose up -d postgres redis minio prometheus grafana

# 3. Run migrations
alembic upgrade head

# 4. Start the API server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 5. Start Celery worker
celery -A app.tasks worker -l info -c 4 -Q default,calls,analytics

# 6. Expose to internet (for Twilio webhooks)
ngrok http 8000
# Update TWILIO_WEBHOOK_BASE_URL in .env with the ngrok URL
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/v1/auth/register | Register tenant + admin user |
| POST | /api/v1/auth/login | Login, get JWT tokens |
| POST | /api/v1/agents | Create AI voice agent |
| GET | /api/v1/agents | List agents |
| POST | /api/v1/calls/outbound | Initiate outbound AI call |
| GET | /api/v1/calls/active | List active calls |
| GET | /api/v1/calls | List call history |
| POST | /api/v1/webhooks/twilio/inbound | Twilio inbound webhook |
| WS | /ws/media-stream/{tenant}/{agent} | Twilio Media Stream |
| GET | /health | Liveness check |
| GET | /ready | Readiness check |
| GET | /metrics | Prometheus metrics |

## Project Structure

```
voice-agent-platform/
│
├── [1]  .env.example
├── [2]  .gitignore
├── [3]  pyproject.toml
├── [4]  docker-compose.yml
├── [5]  README.md
├── [6]  PROJECT_STATE.md
├── [7]  alembic.ini
│
├── alembic/
│   ├── [8]  env.py
│   └── versions/                          ← empty dir, migrations go here
│
├── docker/
│   ├── [9]  Dockerfile
│   └── [10] prometheus.yml
│
├── scripts/
│   └── [11] seed.py
│
├── tests/
│   ├── [12] __init__.py
│   ├── unit/
│   │   ├── [13] __init__.py
│   │   ├── [14] test_security.py
│   │   └── [15] test_pipeline.py
│   └── integration/
│       ├── [16] __init__.py
│       └── [17] test_webhooks.py
│
└── app/
    ├── [18] __init__.py
    ├── [19] config.py
    ├── [20] main.py
    │
    ├── core/
    │   ├── [21] __init__.py
    │   ├── [22] exceptions.py
    │   └── [23] security.py
    │
    ├── db/
    │   ├── [24] __init__.py
    │   ├── [25] session.py
    │   └── repositories/
    │       ├── [26] __init__.py
    │       ├── [27] tenant_repo.py
    │       ├── [28] call_repo.py
    │       └── [29] agent_repo.py
    │
    ├── models/
    │   ├── [30] __init__.py               ← HAS CODE (re-exports models)
    │   ├── [31] base.py
    │   ├── [32] tenant.py
    │   ├── [33] user.py
    │   ├── [34] agent.py
    │   ├── [35] call.py
    │   └── [36] campaign.py
    │
    ├── schemas/
    │   └── [37] __init__.py               ← HAS CODE (all Pydantic schemas)
    │
    ├── middleware/
    │   ├── [38] __init__.py
    │   └── [39] auth.py
    │
    ├── api/
    │   ├── [40] __init__.py
    │   └── v1/
    │       ├── [41] __init__.py
    │       ├── [42] auth.py
    │       ├── [43] agents.py
    │       ├── [44] calls.py
    │       ├── [45] campaigns.py
    │       ├── [46] tenants.py
    │       ├── [47] dashboard.py
    │       ├── [48] exports.py
    │       ├── [49] webhooks.py
    │       └── [50] health.py
    │
    ├── services/
    │   ├── [51] __init__.py
    │   ├── [52] compliance.py
    │   ├── [53] recording.py
    │   └── [54] billing.py
    │
    ├── tasks/
    │   ├── [55] __init__.py               ← HAS CODE (Celery config)
    │   └── [56] call_tasks.py
    │
    ├── utils/
    │   ├── [57] __init__.py
    │   └── [58] resilience.py
    │
    └── voice/
        ├── [59] __init__.py
        ├── [60] pipeline.py
        ├── [61] session_manager.py
        │
        ├── handlers/
        │   ├── [62] __init__.py
        │   └── [63] media_stream.py
        │
        ├── prompts/
        │   └── [64] __init__.py
        │
        └── providers/
            ├── [65] __init__.py
            ├── [66] base.py
            │
            ├── stt/
            │   ├── [67] __init__.py
            │   └── [68] deepgram_provider.py
            │
            ├── llm/
            │   ├── [69] __init__.py
            │   ├── [70] openai_provider.py
            │   └── [71] anthropic_provider.py
            │
            ├── tts/
            │   ├── [72] __init__.py
            │   ├── [73] cartesia_provider.py
            │   └── [74] elevenlabs_provider.py
            │
            └── telephony/
                ├── [75] __init__.py
                ├── [76] twilio_provider.py
                └── [77] plivo_provider.py
```

## Multi-Tenancy

Every tenant-scoped table uses PostgreSQL Row-Level Security (RLS).
The `tenant_id` is extracted from JWT tokens and set as a PostgreSQL session
variable per-request. This prevents data leakage at the database layer.

## License

Proprietary — All rights reserved.