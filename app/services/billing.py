"""
# Billing service — usage metering & plan enforcement
# Usage tracking (Redis):
#   record_call_usage(tenant_id, duration, cost) — atomic increment via pipeline
#   get_current_usage(tenant_id) — current month's minutes, cost, call count
# Plan limits:
#   check_minute_limit(tenant_id, plan) — returns True if under limit
#   check_feature(plan, feature) — feature gating (outbound, recording, analytics)
# Cost estimation:
#   estimate_call_cost(duration, turns, providers) — per-provider rate calculation
# Plans:
#   free: 100 min, 2 concurrent, no outbound
#   pro: 2000 min, 10 concurrent, all features, $99/mo
#   enterprise: 50K min, 100 concurrent, unlimited agents, $499/mo
"""
"""
Billing service — usage metering, cost calculation, and plan limit enforcement.

Tracks per-tenant usage in Redis for real-time enforcement,
with periodic persistence to PostgreSQL for invoicing.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Plan limits
PLAN_LIMITS: dict[str, dict[str, Any]] = {
    "free": {
        "max_monthly_minutes": 100,
        "max_concurrent_calls": 2,
        "max_agents": 1,
        "features": {"outbound": False, "recording": True, "analytics": False},
        "price_usd": 0,
    },
    "pro": {
        "max_monthly_minutes": 2000,
        "max_concurrent_calls": 10,
        "max_agents": 10,
        "features": {"outbound": True, "recording": True, "analytics": True},
        "price_usd": 99,
    },
    "enterprise": {
        "max_monthly_minutes": 50000,
        "max_concurrent_calls": 100,
        "max_agents": -1,  # Unlimited
        "features": {"outbound": True, "recording": True, "analytics": True},
        "price_usd": 499,  # Base price, usage-based on top
    },
}

# Per-minute cost estimates by provider combination
PROVIDER_COSTS_PER_MIN: dict[str, float] = {
    "deepgram": 0.0077,
    "openai": 0.004,
    "anthropic": 0.006,
    "cartesia": 0.005,
    "elevenlabs": 0.008,
    "twilio": 0.014,
    "plivo_us": 0.010,
    "plivo_india": 0.003,
}


class BillingService:
    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    def _month_key(self, tenant_id: str) -> str:
        now = datetime.now(UTC)
        return f"usage:{tenant_id}:{now.year}:{now.month:02d}"

    async def record_call_usage(
        self,
        tenant_id: str,
        duration_seconds: float,
        cost_usd: float,
    ) -> dict[str, Any]:
        """Record call duration and cost for a tenant. Returns updated usage."""
        key = self._month_key(tenant_id)

        pipe = self._redis.pipeline()
        pipe.hincrbyfloat(key, "total_seconds", duration_seconds)
        pipe.hincrbyfloat(key, "total_cost_usd", cost_usd)
        pipe.hincrby(key, "total_calls", 1)
        pipe.expire(key, 45 * 24 * 3600)  # Keep for 45 days
        results = await pipe.execute()

        return {
            "total_seconds": float(results[0]),
            "total_cost_usd": float(results[1]),
            "total_calls": int(results[2]),
        }

    async def get_current_usage(self, tenant_id: str) -> dict[str, Any]:
        """Get current month's usage for a tenant."""
        key = self._month_key(tenant_id)
        data = await self._redis.hgetall(key)
        return {
            "total_minutes": round(float(data.get("total_seconds", 0)) / 60, 1),
            "total_cost_usd": round(float(data.get("total_cost_usd", 0)), 2),
            "total_calls": int(data.get("total_calls", 0)),
        }

    async def check_minute_limit(self, tenant_id: str, plan: str) -> bool:
        """Check if tenant has remaining minutes. Returns True if ALLOWED."""
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        max_minutes = limits["max_monthly_minutes"]
        if max_minutes < 0:
            return True  # Unlimited

        usage = await self.get_current_usage(tenant_id)
        remaining = max_minutes - usage["total_minutes"]

        if remaining <= 0:
            logger.warning(
                "Tenant %s exceeded minute limit: %.1f / %d",
                tenant_id, usage["total_minutes"], max_minutes,
            )
            return False

        if remaining < 10:
            logger.info("Tenant %s approaching limit: %.1f min remaining", tenant_id, remaining)

        return True

    async def check_feature(self, plan: str, feature: str) -> bool:
        """Check if a feature is available on the tenant's plan."""
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        return limits.get("features", {}).get(feature, False)

    def estimate_call_cost(
        self,
        duration_seconds: float,
        turn_count: int,
        stt_provider: str = "deepgram",
        llm_provider: str = "openai",
        tts_provider: str = "cartesia",
        telephony_provider: str = "twilio",
    ) -> float:
        """Estimate the cost of a call based on provider usage."""
        minutes = duration_seconds / 60

        cost = (
            minutes * PROVIDER_COSTS_PER_MIN.get(stt_provider, 0.008)
            + minutes * PROVIDER_COSTS_PER_MIN.get(telephony_provider, 0.014)
            + turn_count * PROVIDER_COSTS_PER_MIN.get(llm_provider, 0.004) * 0.5
            + turn_count * PROVIDER_COSTS_PER_MIN.get(tts_provider, 0.005) * 0.5
        )

        return round(cost, 4)

    def get_plan_limits(self, plan: str) -> dict[str, Any]:
        """Get the limits for a plan."""
        return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])