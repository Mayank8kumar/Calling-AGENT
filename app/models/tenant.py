"""
# Tenant model — top-level organization
# Fields: name, slug (unique), plan (free/pro/enterprise), status (active/suspended/trial)
# Contact: email, phone
# Limits: max_concurrent_calls, max_monthly_minutes
# JSONB fields:
#   provider_config — per-tenant API key overrides {stt: {provider, api_key}, llm: {...}}
#   branding — white-label config {logo_url, accent_color, company_name}
#   features — feature toggles {outbound_enabled, recording_enabled}
# Relationships: users, agents
"""
"""
Tenant model — the top-level multi-tenant entity.
Each tenant represents an organization using the platform.
"""

from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GlobalBase


class TenantPlan(str, Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class TenantStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"


class Tenant(GlobalBase):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(50), default=TenantPlan.FREE, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default=TenantStatus.TRIAL, nullable=False)

    # Contact
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Limits
    max_concurrent_calls: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    max_monthly_minutes: Mapped[int] = mapped_column(Integer, default=500, nullable=False)

    # Provider overrides — tenant can bring their own keys
    provider_config: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        server_default="{}",
        comment="Per-tenant provider overrides: {stt: {provider, api_key}, llm: {...}, tts: {...}}",
    )

    # Branding
    branding: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        server_default="{}",
        comment="White-label config: {logo_url, accent_color, company_name}",
    )

    # Feature flags
    features: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        server_default="{}",
        comment="Feature toggles: {outbound_enabled, recording_enabled, analytics_enabled}",
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    users: Mapped[list["User"]] = relationship(  # noqa: F821
        "User", back_populates="tenant", lazy="selectin"
    )
    agents: Mapped[list["Agent"]] = relationship(  # noqa: F821
        "Agent", back_populates="tenant", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Tenant {self.slug} plan={self.plan}>"