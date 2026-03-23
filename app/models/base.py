"""
# Base model classes
# Base — root DeclarativeBase
# TimestampMixin — created_at, updated_at (auto-managed)
# SoftDeleteMixin — deleted_at, is_deleted, soft_delete()
# TenantBase — for tenant-scoped tables:
#   - UUID primary key (auto-generated)
#   - tenant_id FK to tenants table
#   - Composite index on (tenant_id, created_at)
#   - Inherits timestamps + soft-delete
# GlobalBase — for non-tenant tables (tenants, system config)
"""

# File: voice-agent-platform/app/models/base.py
"""
SQLAlchemy base classes with tenant isolation, timestamps, and soft-delete.

Every tenant-scoped table inherits TenantBase, which enforces:
  - tenant_id FK on every row
  - Composite indexes including tenant_id for query efficiency
  - Automatic timestamp management
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, event, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    """Root declarative base — all models inherit from this."""

    pass


class TimestampMixin:
    """Adds created_at / updated_at columns with auto-management."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Adds soft-delete support via deleted_at column."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        index=True,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        self.deleted_at = datetime.now(UTC)


class TenantBase(Base, TimestampMixin, SoftDeleteMixin):
    """
    Abstract base for all tenant-scoped tables.

    Every table that stores per-tenant data should inherit this.
    Provides: id (UUID PK), tenant_id (FK), created_at, updated_at, deleted_at.
    """

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )

    @declared_attr
    def __table_args__(cls) -> tuple:
        """Default composite index on (tenant_id, created_at) for common query patterns."""
        return (
            Index(
                f"ix_{cls.__tablename__}_tenant_created",
                "tenant_id",
                "created_at",
            ),
        )


class GlobalBase(Base, TimestampMixin):
    """
    Abstract base for global (non-tenant-scoped) tables.
    Used for: tenants, system config, global analytics.
    """

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )