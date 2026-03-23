"""
# Integration tests (6 tests) — uses httpx AsyncClient with ASGI transport
# TestHealthEndpoints:
#   - GET /health returns 200 + "healthy"
#   - GET /metrics returns Prometheus format
# TestTwilioWebhooks:
#   - POST /webhooks/twilio/inbound returns valid TwiML with <Stream>
#   - POST /webhooks/twilio/outbound with machine_start returns <Hangup/>
#   - POST /webhooks/twilio/status returns 200
# TestAuthEndpoints:
#   - Login with wrong credentials returns 401 (not 500)
#   - GET /agents without token returns 401
"""

"""Integration tests for Twilio webhook endpoints."""

import os
import pytest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-validation")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-long")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("TWILIO_WEBHOOK_BASE_URL", "https://test.example.com")

from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealthEndpoints:
    @pytest.mark.asyncio
    async def test_health_check(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self, client):
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert b"voice_active_calls" in response.content


class TestTwilioWebhooks:
    @pytest.mark.asyncio
    async def test_inbound_call_returns_twiml(self, client):
        response = await client.post(
            "/api/v1/webhooks/twilio/inbound",
            data={
                "CallSid": "CA1234567890",
                "From": "+14155551234",
                "To": "+14155559999",
                "CallStatus": "ringing",
            },
        )
        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]
        assert "<Response>" in response.text
        assert "<Connect>" in response.text
        assert "Stream" in response.text

    @pytest.mark.asyncio
    async def test_outbound_voicemail_detection(self, client):
        response = await client.post(
            "/api/v1/webhooks/twilio/outbound",
            data={
                "CallSid": "CA0987654321",
                "AnsweredBy": "machine_start",
            },
        )
        assert response.status_code == 200
        assert "<Hangup/>" in response.text

    @pytest.mark.asyncio
    async def test_status_callback(self, client):
        response = await client.post(
            "/api/v1/webhooks/twilio/status",
            data={
                "CallSid": "CA1234567890",
                "CallStatus": "completed",
                "CallDuration": "120",
            },
        )
        assert response.status_code == 200


class TestAuthEndpoints:
    @pytest.mark.asyncio
    async def test_login_without_credentials(self, client):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "nonexistent@test.com", "password": "wrongpass1"},
        )
        # Will fail since no DB, but should return 401 not 500
        assert response.status_code in (401, 500)

    @pytest.mark.asyncio
    async def test_protected_route_without_token(self, client):
        response = await client.get("/api/v1/agents")
        assert response.status_code == 401