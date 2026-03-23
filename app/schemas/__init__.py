"""
# Pydantic request/response schemas
# Auth: LoginRequest, TokenResponse, RefreshRequest
# Tenant: TenantCreate, TenantResponse
# Agent: AgentCreate, AgentUpdate, AgentResponse
# Call: OutboundCallRequest, CallResponse, CallListResponse, ActiveCallResponse
# Dashboard: DashboardStats
"""
# File: voice-agent-platform/app/schemas/__init__.py
"""
Pydantic schemas for API request/response validation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


# ---------------------------------------------------------------------------
# Tenant
# ---------------------------------------------------------------------------

class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    slug: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")
    email: EmailStr
    phone: str | None = None
    plan: str = "free"


class TenantResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    email: str
    plan: str
    status: str
    max_concurrent_calls: int
    max_monthly_minutes: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    agent_type: str = "inbound"
    system_prompt: str = Field(min_length=10)
    greeting_message: str = "Hello! How can I help you today?"
    language: str = "en"
    voice_id: str | None = None
    stt_provider: str | None = None
    llm_provider: str | None = None
    tts_provider: str | None = None
    llm_model: str | None = None
    llm_temperature: float = 0.7
    llm_max_tokens: int = 200
    max_silence_seconds: int = 10
    max_call_duration_seconds: int = 600
    enable_recording: bool = True
    tools: list[dict[str, Any]] = []
    escalation_config: dict[str, Any] = {}


class AgentUpdate(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    greeting_message: str | None = None
    language: str | None = None
    voice_id: str | None = None
    llm_temperature: float | None = None
    llm_max_tokens: int | None = None
    is_active: bool | None = None
    tools: list[dict[str, Any]] | None = None
    escalation_config: dict[str, Any] | None = None


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    agent_type: str
    is_active: bool
    language: str
    voice_id: str | None
    stt_provider: str | None
    llm_provider: str | None
    tts_provider: str | None
    llm_model: str | None
    llm_temperature: float
    llm_max_tokens: int
    max_silence_seconds: int
    max_call_duration_seconds: int
    enable_recording: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Call
# ---------------------------------------------------------------------------

class OutboundCallRequest(BaseModel):
    agent_id: uuid.UUID
    to_number: str = Field(pattern=r"^\+[1-9]\d{1,14}$")  # E.164 format
    from_number: str | None = None
    metadata: dict[str, Any] = {}


class CallResponse(BaseModel):
    id: uuid.UUID
    call_sid: str | None
    direction: str
    status: str
    outcome: str | None
    from_number: str
    to_number: str
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    turn_count: int
    avg_response_latency_ms: int | None
    sentiment: str | None
    summary: str | None
    estimated_cost_usd: float | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CallListResponse(BaseModel):
    calls: list[CallResponse]
    total: int
    page: int
    page_size: int


class ActiveCallResponse(BaseModel):
    call_id: str
    call_sid: str
    tenant_id: str
    direction: str
    duration_seconds: float
    turns: int


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class DashboardStats(BaseModel):
    active_calls: int
    total_calls_today: int
    total_minutes_today: float
    avg_latency_ms: float
    calls_by_status: dict[str, int]
    calls_by_direction: dict[str, int]