"""
# Database seed script
# Creates tables (CREATE TABLE via SQLAlchemy metadata)
# Enables TimescaleDB extension
# Creates demo data:
#   1. Demo tenant (Pro plan, 10 concurrent, 2000 min/month)
#   2. Admin user: admin@demo.com / admin12345 (owner role)
#   3. Inbound support agent (with tools: lookup_account, transfer_to_human)
#   4. Outbound sales agent (with sales pitch prompt)
# Run: python scripts/seed.py
"""

"""
Database seed script — creates demo tenant, admin user, and sample agent.

Usage: python scripts/seed.py
"""

import asyncio
import uuid
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.config import get_settings
from app.core.security import hash_password
from app.db.session import get_engine, get_session_factory
from app.models import Base
from app.models.tenant import Tenant, TenantPlan, TenantStatus
from app.models.user import User, UserRole
from app.models.agent import Agent, AgentType


async def seed() -> None:
    settings = get_settings()
    engine = get_engine()

    # Create tables (for dev — production uses Alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Enable TimescaleDB extension
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))

    print("Tables created successfully.")

    session_factory = get_session_factory()
    async with session_factory() as session:
        # Check if demo tenant already exists
        from sqlalchemy import select
        result = await session.execute(select(Tenant).where(Tenant.slug == "demo"))
        if result.scalar_one_or_none():
            print("Demo data already exists. Skipping seed.")
            return

        # Create demo tenant
        tenant = Tenant(
            name="Demo Company",
            slug="demo",
            email="admin@demo.com",
            plan=TenantPlan.PRO,
            status=TenantStatus.ACTIVE,
            max_concurrent_calls=10,
            max_monthly_minutes=2000,
            features={"outbound": True, "recording": True, "analytics": True},
        )
        session.add(tenant)
        await session.flush()
        print(f"Created tenant: {tenant.name} (ID: {tenant.id})")

        # Create admin user
        user = User(
            tenant_id=tenant.id,
            email="admin@demo.com",
            hashed_password=hash_password("admin12345"),
            full_name="Demo Admin",
            role=UserRole.OWNER,
        )
        session.add(user)
        await session.flush()
        print(f"Created user: {user.email} (password: admin12345)")

        # Create sample inbound agent
        inbound_agent = Agent(
            tenant_id=tenant.id,
            name="Customer Support Agent",
            agent_type=AgentType.INBOUND,
            system_prompt=(
                "You are a friendly and professional customer support agent for Demo Company. "
                "Help customers with billing questions, account issues, and product information. "
                "Keep responses concise — under 2 sentences. Be warm but efficient. "
                "If you cannot resolve an issue, offer to transfer to a human agent."
            ),
            greeting_message="Hello! Welcome to Demo Company support. How can I help you today?",
            language="en",
            llm_temperature=0.7,
            llm_max_tokens=150,
            max_silence_seconds=10,
            max_call_duration_seconds=600,
            enable_recording=True,
            enable_transcription=True,
            consent_message="This call may be recorded for quality assurance. You can ask to speak with a human at any time.",
            tools=[
                {
                    "name": "lookup_account",
                    "description": "Look up a customer's account by email or phone number",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "identifier": {"type": "string", "description": "Email or phone number"},
                        },
                        "required": ["identifier"],
                    },
                },
                {
                    "name": "transfer_to_human",
                    "description": "Transfer the call to a human agent",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reason": {"type": "string", "description": "Reason for transfer"},
                        },
                        "required": ["reason"],
                    },
                },
            ],
            escalation_config={
                "enabled": True,
                "trigger_phrases": ["speak to a human", "real person", "transfer me"],
                "max_failed_attempts": 3,
            },
        )
        session.add(inbound_agent)
        await session.flush()
        print(f"Created inbound agent: {inbound_agent.name} (ID: {inbound_agent.id})")

        # Create sample outbound agent
        outbound_agent = Agent(
            tenant_id=tenant.id,
            name="Sales Outreach Agent",
            agent_type=AgentType.OUTBOUND,
            system_prompt=(
                "You are a professional sales representative for Demo Company. "
                "You are calling to introduce our new product offering. "
                "Be respectful and not pushy. If the person is not interested, "
                "thank them and end the call politely. Keep your pitch under 30 seconds."
            ),
            greeting_message="Hi! This is Alex from Demo Company. I'm calling about an exciting new offering that might interest you. Do you have a moment?",
            language="en",
            llm_temperature=0.8,
            llm_max_tokens=200,
            max_silence_seconds=8,
            max_call_duration_seconds=300,
            enable_recording=True,
        )
        session.add(outbound_agent)
        await session.flush()
        print(f"Created outbound agent: {outbound_agent.name} (ID: {outbound_agent.id})")

        await session.commit()

    print("\n=== Seed complete! ===")
    print(f"Login credentials: admin@demo.com / admin12345")
    print(f"Tenant ID: {tenant.id}")
    print(f"Inbound Agent ID: {inbound_agent.id}")
    print(f"Outbound Agent ID: {outbound_agent.id}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())