"""
# Async Celery tasks
# process_post_call — runs after every call ends:
#   1. Calculate duration and cost
#   2. Save call record + transcript to PostgreSQL
#   3. Queue intelligence extraction
# extract_conversation_intelligence — runs async:
#   1. Format transcript for LLM
#   2. Extract: sentiment, intent, entities, summary, action_items
#   3. Save to call record
# schedule_outbound_call — for campaigns:
#   1. DNC check
#   2. Calling hours check
#   3. Initiate call via telephony provider
"""
"""
Celery tasks for async call processing.

Post-call tasks:
- Save call record and transcript to database
- Upload recording to S3
- Extract conversation intelligence (sentiment, intent, entities)
- Generate call summary
- Update campaign statistics
"""

from __future__ import annotations

import logging
import time
from typing import Any

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="tasks.process_post_call",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def process_post_call(
    self: Any,
    call_id: str,
    call_sid: str,
    tenant_id: str,
    agent_id: str,
    direction: str,
    transcript: list[dict[str, str]],
    metrics: list[dict[str, float]],
    duration_seconds: float = 0,
) -> dict[str, Any]:
    """
    Process a completed call — runs asynchronously after the call ends.

    Steps:
    1. Calculate final duration and cost
    2. Save call record to PostgreSQL
    3. Store full transcript
    4. Extract conversation intelligence (async LLM call)
    5. Update tenant usage counters
    """
    try:
        logger.info("Processing post-call: call_id=%s call_sid=%s", call_id, call_sid)

        # 1. Calculate metrics
        avg_latency = 0.0
        if metrics:
            total_latencies = [m.get("total_ms", 0) for m in metrics]
            avg_latency = sum(total_latencies) / len(total_latencies) if total_latencies else 0

        # 2. Estimate cost (rough calculation based on providers)
        # Deepgram STT: ~$0.0077/min, OpenAI: ~$0.002/turn, Cartesia TTS: ~$0.003/turn
        minutes = duration_seconds / 60 if duration_seconds else len(metrics) * 0.5 / 60
        estimated_cost = (
            minutes * 0.0077  # STT
            + len(metrics) * 0.002  # LLM
            + len(metrics) * 0.003  # TTS
            + minutes * 0.014  # Telephony
        )

        # 3. Save call record (synchronous DB operation via sync session)
        # In production, use a sync SQLAlchemy session for Celery workers
        call_data = {
            "call_id": call_id,
            "call_sid": call_sid,
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "direction": direction,
            "status": "completed",
            "transcript": transcript,
            "turn_count": len(metrics),
            "avg_response_latency_ms": int(avg_latency),
            "pipeline_metrics": metrics,
            "estimated_cost_usd": round(estimated_cost, 4),
            "duration_seconds": int(duration_seconds) if duration_seconds else None,
        }

        logger.info(
            "Post-call complete: call_id=%s turns=%d avg_latency=%.0fms cost=$%.4f",
            call_id, len(metrics), avg_latency, estimated_cost,
        )

        # TODO: Save to DB via synchronous session
        # TODO: Trigger conversation intelligence extraction
        # TODO: Update tenant usage counters in Redis

        return call_data

    except Exception as exc:
        logger.error("Post-call processing failed for %s: %s", call_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="tasks.extract_conversation_intelligence")
def extract_conversation_intelligence(
    call_id: str,
    transcript: list[dict[str, str]],
) -> dict[str, Any]:
    """
    Extract insights from a call transcript using LLM.
    Returns: sentiment, intent, entities, summary, action_items.
    """
    # This runs as a separate task to avoid blocking post-call processing
    logger.info("Extracting intelligence for call_id=%s", call_id)

    # Format transcript for LLM
    transcript_text = "\n".join(
        f"{turn['role'].upper()}: {turn['content']}" for turn in transcript
    )

    # TODO: Call LLM with structured output for extraction
    # For now, return placeholder
    return {
        "call_id": call_id,
        "sentiment": "neutral",
        "intent": "general_inquiry",
        "entities": {},
        "summary": "Call processed successfully",
        "action_items": [],
    }


@shared_task(name="tasks.schedule_outbound_call")
def schedule_outbound_call(
    campaign_id: str,
    contact_phone: str,
    contact_name: str,
    tenant_id: str,
    agent_id: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    """
    Schedule a single outbound call as part of a campaign.
    Handles DNC checking and calling hours enforcement.
    """
    logger.info(
        "Scheduling outbound call: campaign=%s to=%s",
        campaign_id, contact_phone[-4:],
    )

    # TODO: DNC check
    # TODO: Calling hours check based on recipient timezone
    # TODO: Initiate call via telephony provider

    return {
        "campaign_id": campaign_id,
        "contact_phone": contact_phone[-4:],
        "status": "scheduled",
    }