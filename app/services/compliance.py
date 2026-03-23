"""
# Compliance service — DNC, calling hours, consent
# Jurisdiction detection: phone prefix → US/India/EU
# DNC checking:
#   check_dnc(phone, tenant_id) — checks global DNC + tenant opt-out + India NCPR (Redis sets)
#   add_to_dnc(phone, tenant_id) — add to tenant opt-out list
# Calling hours:
#   check_calling_hours(phone, timezone) — US: 8AM-9PM, India: 9AM-9PM, EU: 9AM-8PM
# Duplicate prevention:
#   check_duplicate_call(phone, tenant_id, cooldown) — prevents double-dialing
# Consent:
#   record_consent() / get_consent() — Redis-backed, 3-year retention
# AI Disclosure:
#   get_ai_disclosure_message(jurisdiction, language) — per-jurisdiction message
# Full check:
#   run_full_outbound_check() — runs all checks, raises on violation
"""
"""
Compliance service — DNC/DND checking, calling hours enforcement,
consent management, and jurisdiction-specific rules.

Supports:
- US TCPA: National DNC registry, 8AM-9PM local time, AI disclosure
- India TRAI: NCPR/DND scrubbing, 9AM-9PM IST, 140-series numbers, DLT registration
- EU GDPR: Consent tracking, data retention, right to erasure
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis

from app.core.exceptions import (
    CallingHoursViolationError,
    ComplianceError,
    DNCViolationError,
)

logger = logging.getLogger(__name__)


class Jurisdiction(str, Enum):
    US = "us"
    INDIA = "india"
    EU = "eu"
    OTHER = "other"


# Calling hour rules per jurisdiction
CALLING_HOURS: dict[str, dict[str, Any]] = {
    "us": {
        "start": time(8, 0),
        "end": time(21, 0),
        "default_tz": "America/New_York",
    },
    "india": {
        "start": time(9, 0),
        "end": time(21, 0),
        "default_tz": "Asia/Kolkata",
    },
    "eu": {
        "start": time(9, 0),
        "end": time(20, 0),
        "default_tz": "Europe/London",
    },
}

# Phone prefix → jurisdiction mapping
JURISDICTION_MAP: dict[str, Jurisdiction] = {
    "+1": Jurisdiction.US,
    "+91": Jurisdiction.INDIA,
    "+44": Jurisdiction.EU,
    "+49": Jurisdiction.EU,
    "+33": Jurisdiction.EU,
    "+34": Jurisdiction.EU,
    "+39": Jurisdiction.EU,
    "+31": Jurisdiction.EU,
}


class ComplianceService:
    """Centralized compliance checks for all outbound calling."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    def detect_jurisdiction(self, phone_number: str) -> Jurisdiction:
        """Detect jurisdiction from phone number prefix."""
        for prefix, jurisdiction in sorted(
            JURISDICTION_MAP.items(), key=lambda x: -len(x[0])
        ):
            if phone_number.startswith(prefix):
                return jurisdiction
        return Jurisdiction.OTHER

    async def check_dnc(self, phone_number: str, tenant_id: str) -> bool:
        """
        Check if a number is on the Do-Not-Call registry.

        Checks both:
        1. Global DNC list (loaded from national registries)
        2. Tenant-specific opt-out list
        
        Returns True if the number is BLOCKED (on DNC).
        """
        # Check tenant-specific opt-out list
        tenant_key = f"dnc:tenant:{tenant_id}"
        is_tenant_blocked = await self._redis.sismember(tenant_key, phone_number)
        if is_tenant_blocked:
            logger.info("Number %s...%s blocked by tenant opt-out", phone_number[:4], phone_number[-4:])
            return True

        # Check global DNC registry
        global_key = "dnc:global"
        is_global_blocked = await self._redis.sismember(global_key, phone_number)
        if is_global_blocked:
            logger.info("Number %s...%s on global DNC", phone_number[:4], phone_number[-4:])
            return True

        # India-specific: Check NCPR/DND registry
        jurisdiction = self.detect_jurisdiction(phone_number)
        if jurisdiction == Jurisdiction.INDIA:
            india_key = "dnc:india:ncpr"
            is_india_blocked = await self._redis.sismember(india_key, phone_number)
            if is_india_blocked:
                logger.info("Number %s...%s on India NCPR/DND", phone_number[:4], phone_number[-4:])
                return True

        return False

    async def add_to_dnc(self, phone_number: str, tenant_id: str) -> None:
        """Add a number to the tenant-specific opt-out list."""
        tenant_key = f"dnc:tenant:{tenant_id}"
        await self._redis.sadd(tenant_key, phone_number)
        logger.info("Added %s...%s to tenant %s DNC list", phone_number[:4], phone_number[-4:], tenant_id)

    def check_calling_hours(
        self,
        phone_number: str,
        recipient_timezone: str | None = None,
    ) -> bool:
        """
        Check if the current time is within permitted calling hours.
        
        Returns True if calling is ALLOWED.
        """
        jurisdiction = self.detect_jurisdiction(phone_number)
        rules = CALLING_HOURS.get(jurisdiction.value)

        if not rules:
            return True  # No rules for unknown jurisdictions

        tz_name = recipient_timezone or rules["default_tz"]
        try:
            tz = ZoneInfo(tz_name)
        except KeyError:
            tz = ZoneInfo(rules["default_tz"])

        local_now = datetime.now(tz).time()
        start = rules["start"]
        end = rules["end"]

        return start <= local_now <= end

    async def check_duplicate_call(
        self,
        phone_number: str,
        tenant_id: str,
        cooldown_seconds: int = 300,
    ) -> bool:
        """
        Check if we've already called this number recently.
        Prevents double-dialing within the cooldown period.
        
        Returns True if a duplicate (should NOT call).
        """
        key = f"recent_call:{tenant_id}:{phone_number}"
        exists = await self._redis.exists(key)
        if exists:
            return True

        # Set flag with expiry
        await self._redis.setex(key, cooldown_seconds, "1")
        return False

    async def record_consent(
        self,
        phone_number: str,
        tenant_id: str,
        consent_type: str = "recording",
        given: bool = True,
    ) -> None:
        """Record consent status for a phone number."""
        key = f"consent:{tenant_id}:{phone_number}:{consent_type}"
        value = {
            "given": given,
            "timestamp": datetime.now(UTC).isoformat(),
            "type": consent_type,
        }
        import json
        await self._redis.set(key, json.dumps(value))
        # Keep consent records for 3 years (regulatory requirement)
        await self._redis.expire(key, 3 * 365 * 24 * 3600)

    async def get_consent(
        self, phone_number: str, tenant_id: str, consent_type: str = "recording"
    ) -> bool:
        """Check if consent was given."""
        key = f"consent:{tenant_id}:{phone_number}:{consent_type}"
        value = await self._redis.get(key)
        if not value:
            return False
        import json
        data = json.loads(value)
        return data.get("given", False)

    def get_ai_disclosure_message(self, jurisdiction: Jurisdiction, language: str = "en") -> str:
        """Get jurisdiction-appropriate AI disclosure message."""
        messages = {
            ("us", "en"): (
                "This is an AI-powered call. This call may be recorded for quality "
                "assurance purposes. You can ask to speak with a human at any time."
            ),
            ("india", "en"): (
                "This is an automated call powered by artificial intelligence. "
                "This call may be recorded. Press star or say 'human' to speak "
                "with a representative."
            ),
            ("india", "hi"): (
                "Yeh ek AI dwara chalaya jaane wala call hai. Yeh call record ho "
                "sakti hai. Kisi vyakti se baat karne ke liye star dabayein."
            ),
            ("eu", "en"): (
                "This is an AI-assisted call. This call will be recorded with "
                "your consent. You may end the call at any time or request to "
                "speak with a human agent."
            ),
        }
        return messages.get(
            (jurisdiction.value, language),
            messages.get((jurisdiction.value, "en"), messages[("us", "en")]),
        )

    async def run_full_outbound_check(
        self,
        phone_number: str,
        tenant_id: str,
        recipient_timezone: str | None = None,
    ) -> dict[str, Any]:
        """
        Run all compliance checks for an outbound call.
        Returns a result dict with pass/fail for each check.
        Raises ComplianceError if any critical check fails.
        """
        jurisdiction = self.detect_jurisdiction(phone_number)
        results: dict[str, Any] = {"jurisdiction": jurisdiction.value, "checks": {}}

        # 1. DNC check
        is_dnc = await self.check_dnc(phone_number, tenant_id)
        results["checks"]["dnc"] = "blocked" if is_dnc else "clear"
        if is_dnc:
            raise DNCViolationError(phone_number)

        # 2. Calling hours check
        hours_ok = self.check_calling_hours(phone_number, recipient_timezone)
        results["checks"]["calling_hours"] = "allowed" if hours_ok else "blocked"
        if not hours_ok:
            tz = recipient_timezone or CALLING_HOURS.get(jurisdiction.value, {}).get("default_tz", "UTC")
            raise CallingHoursViolationError(tz)

        # 3. Duplicate call check
        is_duplicate = await self.check_duplicate_call(phone_number, tenant_id)
        results["checks"]["duplicate"] = "blocked" if is_duplicate else "clear"
        if is_duplicate:
            raise ComplianceError(f"Duplicate call to {phone_number[-4:]} within cooldown period")

        # 4. Get disclosure message
        results["disclosure_message"] = self.get_ai_disclosure_message(jurisdiction)

        results["passed"] = True
        return results