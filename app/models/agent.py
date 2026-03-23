"""
# Agent model — AI voice agent configuration
# Identity: name, agent_type (inbound/outbound/hybrid), is_active
# Voice: system_prompt, greeting_message, voice_id, language
# Provider overrides: stt_provider, llm_provider, tts_provider, llm_model
# Tuning: llm_temperature, llm_max_tokens, max_silence_seconds, max_call_duration_seconds
# Recording: enable_recording, enable_transcription, consent_message
# JSONB fields:
#   tools — function calling definitions [{name, description, parameters}]
#   escalation_config — {enabled, phone_number, trigger_phrases, max_attempts}
#   knowledge_base — {documents, faq_url, api_endpoints}
"""

"""
Agent model — configurable AI voice agent definitions per tenant.
Each agent has its own personality, prompt, voice, and provider config.
"""

from __future__ import annotations

from enum import Enum

from sqlalchemy import Boolean, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TenantBase


class AgentType(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    HYBRID = "hybrid"


class Agent(TenantBase):
    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(50), default=AgentType.INBOUND, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Voice & personality
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    greeting_message: Mapped[str] = mapped_column(
        String(500), default="Hello! How can I help you today?", nullable=False
    )
    voice_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="TTS voice ID override"
    )
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)

    # Provider overrides (inherits from tenant if null)
    stt_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    llm_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tts_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    llm_temperature: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    llm_max_tokens: Mapped[int] = mapped_column(Integer, default=200, nullable=False)

    # Behavior config
    max_silence_seconds: Mapped[int] = mapped_column(
        Integer, default=10, nullable=False,
        comment="Seconds of silence before prompting user",
    )
    max_call_duration_seconds: Mapped[int] = mapped_column(
        Integer, default=600, nullable=False, comment="Hard limit on call duration"
    )
    enable_recording: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enable_transcription: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    consent_message: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Recording consent announcement played at call start",
    )

    # Tool/function calling config
    tools: Mapped[dict] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
        server_default="[]",
        comment="Available function-calling tools: [{name, description, parameters}]",
    )

    # Escalation config
    escalation_config: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        server_default="{}",
        comment="Human transfer config: {enabled, phone_number, trigger_phrases, max_attempts}",
    )

    # Knowledge base references
    knowledge_base: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        server_default="{}",
        comment="RAG/knowledge references: {documents: [], faq_url, api_endpoints: []}",
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="agents")  # noqa: F821

    __table_args__ = (
        Index("ix_agents_tenant_type", "tenant_id", "agent_type"),
        Index("ix_agents_tenant_active", "tenant_id", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Agent {self.name} type={self.agent_type}>"