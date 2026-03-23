"""
# Health & monitoring routes (no auth required)
# GET /health — liveness probe (just returns "healthy")
# GET /ready — readiness probe (checks PostgreSQL + Redis connectivity)
# GET /metrics — Prometheus metrics (active calls gauge, call duration histogram, etc.)
"""
"""
Health check, readiness, and monitoring endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

from app.db.session import get_engine, get_redis
from app.voice.session_manager import call_manager

router = APIRouter(tags=["health"])

# Prometheus metrics
ACTIVE_CALLS = Gauge("voice_active_calls", "Number of active voice calls", ["tenant_id"])
CALL_DURATION = Histogram(
    "voice_call_duration_seconds", "Call duration in seconds",
    buckets=[30, 60, 120, 300, 600, 900, 1800],
)
PIPELINE_LATENCY = Histogram(
    "voice_pipeline_latency_ms", "AI pipeline latency per turn",
    ["stage"],
    buckets=[50, 100, 200, 300, 500, 800, 1000, 2000],
)
CALL_TOTAL = Counter("voice_calls_total", "Total calls processed", ["direction", "status"])
ERRORS_TOTAL = Counter("voice_errors_total", "Total errors", ["component", "error_type"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Liveness probe — confirms the service is running."""
    return {"status": "healthy", "service": "voice-agent-platform"}


@router.get("/ready")
async def readiness_check() -> dict[str, Any]:
    """
    Readiness probe — confirms all dependencies are reachable.
    Returns 503 if any critical dependency is down.
    """
    checks: dict[str, str] = {}

    # Check PostgreSQL
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute("SELECT 1")  # type: ignore
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # Check Redis
    try:
        redis = get_redis()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    # Active calls info
    checks["active_calls"] = str(call_manager.get_active_count())

    all_ok = all(v == "ok" for k, v in checks.items() if k != "active_calls")

    if not all_ok:
        return Response(
            content={"status": "unhealthy", "checks": checks},
            status_code=503,
        )

    return {"status": "ready", "checks": checks}


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    """Expose Prometheus metrics."""
    # Update active calls gauge
    ACTIVE_CALLS.labels(tenant_id="all").set(call_manager.get_active_count())

    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )