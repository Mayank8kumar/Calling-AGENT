"""
# Dashboard & analytics routes (all require auth)
# GET /dashboard/stats — real-time dashboard:
#   - Active calls count + session details (from session_manager)
#   - Today's stats (total calls, minutes, avg latency, by status/direction)
#   - Monthly usage (from Redis billing service)
# GET /dashboard/analytics — analytics for past N days
"""

"""
Dashboard API — real-time stats, analytics, and monitoring.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.call_repo import CallRepository
from app.db.session import get_db, get_redis
from app.middleware.auth import get_current_user, get_tenant_id
from app.services.billing import BillingService
from app.voice.session_manager import call_manager

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats")
async def get_dashboard_stats(
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get comprehensive dashboard statistics."""
    call_repo = CallRepository(db)
    redis = get_redis()
    billing = BillingService(redis)

    # Real-time active calls
    active_calls = call_manager.get_active_count(tenant_id)
    active_sessions = call_manager.get_active_sessions(tenant_id)

    # Today's stats from DB
    today_stats = await call_repo.get_today_stats(uuid.UUID(tenant_id))

    # Monthly usage from Redis
    monthly_usage = await billing.get_current_usage(tenant_id)

    return {
        "active_calls": active_calls,
        "active_sessions": active_sessions,
        **today_stats,
        "monthly_usage": monthly_usage,
    }


@router.get("/analytics")
async def get_analytics(
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    days: int = 7,
) -> dict[str, Any]:
    """Get analytics for the past N days."""
    call_repo = CallRepository(db)
    monthly = await call_repo.get_monthly_usage(uuid.UUID(tenant_id))
    return {
        "period_days": days,
        "monthly_summary": monthly,
    }