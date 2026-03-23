"""
# Model registry — imports all models so Alembic can discover them
# Exports: Base, Tenant, User, Agent, Call, Campaign + all enums
"""
# File: voice-agent-platform/app/models/__init__.py
"""
Models package — import all models here for Alembic autogenerate discovery.
"""

from app.models.base import Base, GlobalBase, TenantBase
from app.models.tenant import Tenant, TenantPlan, TenantStatus
from app.models.user import User, UserRole
from app.models.agent import Agent, AgentType
from app.models.call import Call, CallDirection, CallOutcome, CallStatus
from app.models.campaign import Campaign, CampaignStatus

__all__ = [
    "Base",
    "GlobalBase",
    "TenantBase",
    "Tenant",
    "TenantPlan",
    "TenantStatus",
    "User",
    "UserRole",
    "Agent",
    "AgentType",
    "Call",
    "CallDirection",
    "CallOutcome",
    "CallStatus",
    "Campaign",
    "CampaignStatus",
]