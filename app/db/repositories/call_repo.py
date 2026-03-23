"""
# Call data access layer — CRUD + analytics
# CRUD:
#   get_by_id(call_id, tenant_id)
#   get_by_call_sid(call_sid) — lookup by Twilio/Plivo external ID
#   create(**kwargs)
#   update_status(call_id, status, **extra)
#   save_post_call_data(call_sid, transcript, metrics, duration, cost)
#   save_intelligence(call_id, sentiment, intent, entities, summary, action_items)
# Query:
#   list_calls(tenant_id, filters, pagination) — returns (calls, total)
# Analytics:
#   get_today_stats(tenant_id) — total calls, minutes, latency, by status/direction
#   get_monthly_usage(tenant_id) — calls, minutes, cost for current month
"""
"""
Call repository — data access layer for call records, analytics, and search.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call, CallDirection, CallStatus


class CallRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(
        self, call_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Call | None:
        result = await self._db.execute(
            select(Call)
            .where(Call.id == call_id)
            .where(Call.tenant_id == tenant_id)
            .where(Call.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_call_sid(self, call_sid: str) -> Call | None:
        result = await self._db.execute(
            select(Call).where(Call.telephony_call_sid == call_sid)
        )
        return result.scalar_one_or_none()

    async def create(self, **kwargs: Any) -> Call:
        call = Call(**kwargs)
        self._db.add(call)
        await self._db.flush()
        await self._db.refresh(call)
        return call

    async def update_status(
        self,
        call_id: uuid.UUID,
        status: str,
        **extra: Any,
    ) -> Call | None:
        call = await self._db.get(Call, call_id)
        if not call:
            return None
        call.status = status
        for key, value in extra.items():
            if hasattr(call, key):
                setattr(call, key, value)
        await self._db.flush()
        await self._db.refresh(call)
        return call

    async def save_post_call_data(
        self,
        call_sid: str,
        transcript: list[dict[str, str]],
        metrics: list[dict[str, float]],
        duration_seconds: int,
        estimated_cost: float,
    ) -> Call | None:
        """Save post-call processing results."""
        call = await self.get_by_call_sid(call_sid)
        if not call:
            return None

        avg_latency = 0
        if metrics:
            totals = [m.get("total_ms", 0) for m in metrics]
            avg_latency = int(sum(totals) / len(totals)) if totals else 0

        call.status = CallStatus.COMPLETED
        call.transcript = transcript
        call.pipeline_metrics = metrics
        call.turn_count = len(metrics)
        call.avg_response_latency_ms = avg_latency
        call.duration_seconds = duration_seconds
        call.estimated_cost_usd = estimated_cost
        call.ended_at = datetime.now(UTC)

        await self._db.flush()
        await self._db.refresh(call)
        return call

    async def save_intelligence(
        self,
        call_id: uuid.UUID,
        sentiment: str | None = None,
        intent: str | None = None,
        entities: dict | None = None,
        summary: str | None = None,
        action_items: list | None = None,
    ) -> Call | None:
        call = await self._db.get(Call, call_id)
        if not call:
            return None
        if sentiment:
            call.sentiment = sentiment
        if intent:
            call.intent = intent
        if entities:
            call.entities = entities
        if summary:
            call.summary = summary
        if action_items:
            call.action_items = action_items
        await self._db.flush()
        return call

    async def list_calls(
        self,
        tenant_id: uuid.UUID,
        offset: int = 0,
        limit: int = 20,
        direction: str | None = None,
        status: str | None = None,
        agent_id: uuid.UUID | None = None,
        campaign_id: uuid.UUID | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> tuple[list[Call], int]:
        """List calls with filtering and pagination. Returns (calls, total_count)."""
        query = (
            select(Call)
            .where(Call.tenant_id == tenant_id)
            .where(Call.deleted_at.is_(None))
        )
        count_query = (
            select(func.count(Call.id))
            .where(Call.tenant_id == tenant_id)
            .where(Call.deleted_at.is_(None))
        )

        # Apply filters
        if direction:
            query = query.where(Call.direction == direction)
            count_query = count_query.where(Call.direction == direction)
        if status:
            query = query.where(Call.status == status)
            count_query = count_query.where(Call.status == status)
        if agent_id:
            query = query.where(Call.agent_id == agent_id)
            count_query = count_query.where(Call.agent_id == agent_id)
        if campaign_id:
            query = query.where(Call.campaign_id == campaign_id)
            count_query = count_query.where(Call.campaign_id == campaign_id)
        if from_date:
            query = query.where(Call.created_at >= from_date)
            count_query = count_query.where(Call.created_at >= from_date)
        if to_date:
            query = query.where(Call.created_at <= to_date)
            count_query = count_query.where(Call.created_at <= to_date)

        # Execute
        total_result = await self._db.execute(count_query)
        total = total_result.scalar() or 0

        result = await self._db.execute(
            query.order_by(desc(Call.created_at)).offset(offset).limit(limit)
        )
        calls = list(result.scalars().all())

        return calls, total

    # --- Analytics queries ---

    async def get_today_stats(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """Get today's call statistics for dashboard."""
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

        # Total calls today
        total_result = await self._db.execute(
            select(func.count(Call.id))
            .where(Call.tenant_id == tenant_id)
            .where(Call.created_at >= today_start)
        )
        total_calls = total_result.scalar() or 0

        # Total minutes today
        minutes_result = await self._db.execute(
            select(func.coalesce(func.sum(Call.duration_seconds), 0))
            .where(Call.tenant_id == tenant_id)
            .where(Call.created_at >= today_start)
        )
        total_seconds = minutes_result.scalar() or 0

        # Average latency
        latency_result = await self._db.execute(
            select(func.avg(Call.avg_response_latency_ms))
            .where(Call.tenant_id == tenant_id)
            .where(Call.created_at >= today_start)
            .where(Call.avg_response_latency_ms.isnot(None))
        )
        avg_latency = latency_result.scalar() or 0

        # Calls by status
        status_result = await self._db.execute(
            select(Call.status, func.count(Call.id))
            .where(Call.tenant_id == tenant_id)
            .where(Call.created_at >= today_start)
            .group_by(Call.status)
        )
        by_status = {row[0]: row[1] for row in status_result.all()}

        # Calls by direction
        direction_result = await self._db.execute(
            select(Call.direction, func.count(Call.id))
            .where(Call.tenant_id == tenant_id)
            .where(Call.created_at >= today_start)
            .group_by(Call.direction)
        )
        by_direction = {row[0]: row[1] for row in direction_result.all()}

        return {
            "total_calls_today": total_calls,
            "total_minutes_today": round(total_seconds / 60, 1),
            "avg_latency_ms": round(avg_latency, 0),
            "calls_by_status": by_status,
            "calls_by_direction": by_direction,
        }

    async def get_monthly_usage(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """Get current month usage for billing."""
        month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        minutes_result = await self._db.execute(
            select(func.coalesce(func.sum(Call.duration_seconds), 0))
            .where(Call.tenant_id == tenant_id)
            .where(Call.created_at >= month_start)
        )
        total_seconds = minutes_result.scalar() or 0

        cost_result = await self._db.execute(
            select(func.coalesce(func.sum(Call.estimated_cost_usd), 0))
            .where(Call.tenant_id == tenant_id)
            .where(Call.created_at >= month_start)
        )
        total_cost = cost_result.scalar() or 0

        count_result = await self._db.execute(
            select(func.count(Call.id))
            .where(Call.tenant_id == tenant_id)
            .where(Call.created_at >= month_start)
        )
        total_calls = count_result.scalar() or 0

        return {
            "total_calls": total_calls,
            "total_minutes": round(total_seconds / 60, 1),
            "total_cost_usd": round(total_cost, 2),
        }