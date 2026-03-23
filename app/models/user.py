"""
# User model — tenant-scoped users
# Fields: email, hashed_password, full_name, role, is_active, phone_number
# Roles: owner, admin, agent (human agent for transfers), viewer
# Inherits TenantBase — has tenant_id FK
"""
"""
User model — tenant-scoped users with RBAC.
"""

from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TenantBase


class UserRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    AGENT = "agent"  # Human agent who can receive transfers
    VIEWER = "viewer"


class User(TenantBase):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default=UserRole.VIEWER, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Optional: phone for human agent transfer
    phone_number: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="users")  # noqa: F821

    def __repr__(self) -> str:
        return f"<User {self.email} role={self.role}>"