"""
# Circuit breaker & provider fallback
# CircuitBreaker class:
#   States: CLOSED (normal) → OPEN (failing, reject) → HALF_OPEN (test one request)
#   Trips after 5 failures, auto-resets after 30s cooldown
# call_with_fallback():
#   1. Try primary provider with timeout
#   2. If fails or circuit open → try fallback provider
#   3. Both fail → raise RuntimeError
# Usage: wrap any STT/LLM/TTS call for automatic failover
"""

"""
Resilience utilities: circuit breaker and provider fallback chains.

The fallback chain wraps provider calls with:
1. Timeout enforcement
2. Circuit breaker (trips after N failures, auto-resets after cooldown)
3. Automatic fallback to secondary provider on failure
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreaker:
    """
    Simple async circuit breaker.

    - CLOSED: normal, count failures
    - OPEN: after failure_threshold, reject immediately for cooldown_seconds
    - HALF_OPEN: allow one test request after cooldown
    """

    name: str
    failure_threshold: int = 5
    cooldown_seconds: float = 30.0
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.monotonic() - self.last_failure_time >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit %s: OPEN → HALF_OPEN", self.name)
                return True
            return False
        # HALF_OPEN — allow one attempt
        return True

    def record_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            logger.info("Circuit %s: HALF_OPEN → CLOSED", self.name)
        self.state = CircuitState.CLOSED
        self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit %s: CLOSED → OPEN (failures: %d)", self.name, self.failure_count
            )


# Global circuit breakers per provider
_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(provider_name: str) -> CircuitBreaker:
    if provider_name not in _breakers:
        _breakers[provider_name] = CircuitBreaker(name=provider_name)
    return _breakers[provider_name]


async def call_with_fallback(
    primary_fn: Callable[..., Any],
    fallback_fn: Callable[..., Any] | None = None,
    primary_name: str = "primary",
    fallback_name: str = "fallback",
    timeout_seconds: float = 10.0,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """
    Execute primary_fn with circuit breaker and timeout.
    Falls back to fallback_fn if primary fails or circuit is open.
    """
    breaker = get_breaker(primary_name)

    if breaker.can_execute():
        try:
            result = await asyncio.wait_for(
                primary_fn(*args, **kwargs), timeout=timeout_seconds
            )
            breaker.record_success()
            return result
        except asyncio.TimeoutError:
            logger.warning("Provider %s timed out after %.1fs", primary_name, timeout_seconds)
            breaker.record_failure()
        except Exception as e:
            logger.warning("Provider %s failed: %s", primary_name, e)
            breaker.record_failure()
    else:
        logger.info("Circuit %s is OPEN — skipping to fallback", primary_name)

    # Try fallback
    if fallback_fn:
        fallback_breaker = get_breaker(fallback_name)
        if fallback_breaker.can_execute():
            try:
                result = await asyncio.wait_for(
                    fallback_fn(*args, **kwargs), timeout=timeout_seconds
                )
                fallback_breaker.record_success()
                logger.info("Fallback %s succeeded", fallback_name)
                return result
            except Exception as e:
                logger.error("Fallback %s also failed: %s", fallback_name, e)
                fallback_breaker.record_failure()
                raise

    raise RuntimeError(f"All providers failed: {primary_name}, {fallback_name}")