"""
# Call model — tracks every voice interaction
# Identity: agent_id FK, campaign_id FK, direction (inbound/outbound)
# Status: queued → ringing → in_progress → completed/failed/transferred
# Outcome: resolved, escalated, dropped, sale_completed, appointment_booked, etc.
# Phone: from_number, to_number, telephony_provider, telephony_call_sid
# Timing: started_at, answered_at, ended_at, duration_seconds, billable_seconds
# Recording: recording_url, recording_s3_key, recording_consent_given
# Transcript: transcript JSONB [{role, content, timestamp}], summary
# Intelligence: sentiment, intent, entities JSONB, action_items JSONB
# Metrics: avg_response_latency_ms, turn_count, pipeline_metrics JSONB
# Cost: estimated_cost_usd
# Errors: error_code, error_message
# Metadata: arbitrary KV JSONB (campaign tags, CRM refs, UTM params)
"""
"""
Call model — the central entity tracking every voice interaction.
Stores lifecycle state, metadata, AI pipeline performance, and outcomes.
"""

from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TenantBase


class CallDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CallStatus(str, Enum):
    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    CANCELED = "canceled"
    VOICEMAIL = "voicemail"
    TRANSFERRED = "transferred"


class CallOutcome(str, Enum):
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    DROPPED = "dropped"
    VOICEMAIL_LEFT = "voicemail_left"
    CALLBACK_SCHEDULED = "callback_scheduled"
    SALE_COMPLETED = "sale_completed"
    APPOINTMENT_BOOKED = "appointment_booked"
    NO_OUTCOME = "no_outcome"


class Call(TenantBase):
    __tablename__ = "calls"

    # Identity
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Call metadata
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default=CallStatus.QUEUED, nullable=False, index=True
    )
    outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Phone numbers (encrypted at rest for PII compliance)
    from_number: Mapped[str] = mapped_column(String(50), nullable=False)
    to_number: Mapped[str] = mapped_column(String(50), nullable=False)

    # Telephony provider references
    telephony_provider: Mapped[str] = mapped_column(
        String(50), default="twilio", nullable=False
    )
    telephony_call_sid: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True,
        comment="External call ID from Twilio/Plivo",
    )

    # Timing
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    billable_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Recording
    recording_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    recording_s3_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    recording_consent_given: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Transcript
    transcript: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment="Full conversation transcript: [{role, content, timestamp}]",
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Conversation intelligence
    sentiment: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="Overall sentiment: positive/neutral/negative"
    )
    intent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entities: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="Extracted entities: {name, email, order_id, ...}"
    )
    action_items: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="Action items extracted from call"
    )

    # Performance metrics
    avg_response_latency_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="Average AI pipeline latency per turn"
    )
    turn_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pipeline_metrics: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment="Per-turn latency breakdown: [{stt_ms, llm_ms, tts_ms, total_ms}]",
    )

    # Cost tracking
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Error tracking
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadata
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False, server_default="{}",
        comment="Arbitrary KV metadata: campaign tags, CRM refs, UTM params",
    )

    # Relationships
    agent: Mapped["Agent | None"] = relationship("Agent", lazy="selectin")  # noqa: F821

    __table_args__ = (
        Index("ix_calls_tenant_status", "tenant_id", "status"),
        Index("ix_calls_tenant_direction", "tenant_id", "direction"),
        Index("ix_calls_tenant_created", "tenant_id", "created_at"),
        Index("ix_calls_agent_created", "agent_id", "created_at"),
        Index("ix_calls_campaign", "campaign_id"),
    )

    def __repr__(self) -> str:
        return f"<Call {self.id} {self.direction} status={self.status}>"