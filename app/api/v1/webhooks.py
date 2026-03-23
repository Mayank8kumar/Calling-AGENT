"""
# Twilio webhook + WebSocket routes
# POST /webhooks/twilio/inbound — Twilio calls this when someone calls your number
#   1. Looks up tenant by phone number
#   2. Returns TwiML: consent message + <Connect><Stream> to WebSocket
# POST /webhooks/twilio/outbound — Twilio calls this when outbound call is answered
#   1. Checks for voicemail (AMD) — hangs up if machine
#   2. Returns TwiML connecting to WebSocket
# POST /webhooks/twilio/status — call status updates (ringing, answered, completed)
# WS   /ws/media-stream/{tenant_id}/{agent_id} — THE MAIN AUDIO HANDLER
#   - Twilio connects here after TwiML <Stream>
#   - Creates call session + voice pipeline
#   - Bidirectional audio: Twilio ↔ STT ↔ LLM ↔ TTS ↔ Twilio
"""
"""
Twilio webhook and WebSocket routes.

Endpoints:
- POST /webhooks/twilio/inbound  — Twilio hits this when an inbound call arrives
- POST /webhooks/twilio/outbound — Twilio hits this when an outbound call connects
- POST /webhooks/twilio/status   — Call status callback
- WS   /ws/media-stream/{tenant_id}/{agent_id} — Media Streams WebSocket
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Form, Query, Request, Response, WebSocket

from app.config import get_settings
from app.voice.handlers.media_stream import TwilioMediaStreamHandler
from app.voice.pipeline import PipelineConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/twilio", tags=["twilio-webhooks"])
ws_router = APIRouter(tags=["websocket"])


def _build_pipeline_config(tenant_id: str, agent_id: str) -> PipelineConfig:
    """
    Build a PipelineConfig from tenant + agent configuration.
    In production, this loads from database. For now, uses env defaults.
    """
    settings = get_settings()

    return PipelineConfig(
        stt_provider="deepgram",
        llm_provider="openai",
        tts_provider="cartesia",
        stt_config={
            "api_key": settings.deepgram_api_key,
            "model": settings.deepgram_model,
            "language": settings.deepgram_language,
            "sample_rate": 8000,
            "encoding": "mulaw",
        },
        llm_config={
            "api_key": settings.openai_api_key,
            "model": settings.openai_model,
        },
        tts_config={
            "api_key": settings.cartesia_api_key,
            "voice_id": settings.cartesia_voice_id,
            "model": settings.cartesia_model,
            "language": settings.cartesia_language,
            "sample_rate": 8000,
            "output_encoding": "pcm_mulaw",
        },
        system_prompt="You are a helpful voice assistant for customer support. Keep responses concise and conversational — under 2 sentences when possible. Be warm and professional.",
        greeting_message="Hello! Thanks for calling. How can I help you today?",
        llm_temperature=settings.openai_temperature,
        llm_max_tokens=settings.openai_max_tokens,
    )


@router.post("/inbound")
async def handle_inbound_call(
    request: Request,
    CallSid: str = Form(""),
    From: str = Form(""),
    To: str = Form(""),
    CallStatus: str = Form(""),
) -> Response:
    """
    Handle incoming call from Twilio.
    Returns TwiML that connects to our Media Streams WebSocket.
    """
    settings = get_settings()
    logger.info(
        "Inbound call: SID=%s from=%s to=%s status=%s",
        CallSid, From, To, CallStatus,
    )

    # TODO: Look up tenant by phone number (To)
    # For now, use a default tenant
    tenant_id = "default"
    agent_id = "default"

    # Build WebSocket URL for Media Streams
    ws_base = settings.twilio_webhook_base_url.replace("https://", "wss://").replace(
        "http://", "ws://"
    )
    ws_url = f"{ws_base}/ws/media-stream/{tenant_id}/{agent_id}"

    # Generate TwiML with optional consent message
    from app.voice.providers.telephony.twilio_provider import TwilioTelephonyProvider

    consent_msg = "This call may be recorded for quality assurance purposes."
    twiml = TwilioTelephonyProvider.generate_media_stream_twiml(
        websocket_url=ws_url,
        call_sid=CallSid,
        consent_message=consent_msg,
    )

    return Response(content=twiml, media_type="application/xml")


@router.post("/outbound")
async def handle_outbound_connected(
    request: Request,
    CallSid: str = Form(""),
    AnsweredBy: str = Form(""),
) -> Response:
    """
    Handle outbound call connection (Twilio hits this when the recipient answers).
    """
    settings = get_settings()
    logger.info("Outbound call connected: SID=%s answered_by=%s", CallSid, AnsweredBy)

    # Voicemail detection
    if AnsweredBy in ("machine_start", "machine_end_beep", "machine_end_silence", "fax"):
        logger.info("Voicemail/machine detected for %s — handling accordingly", CallSid)
        # TODO: Leave voicemail or hang up based on campaign config
        twiml = "<Response><Hangup/></Response>"
        return Response(content=twiml, media_type="application/xml")

    # TODO: Look up call metadata for tenant/agent IDs
    tenant_id = "default"
    agent_id = "default"

    ws_base = settings.twilio_webhook_base_url.replace("https://", "wss://").replace(
        "http://", "ws://"
    )
    ws_url = f"{ws_base}/ws/media-stream/{tenant_id}/{agent_id}"

    from app.voice.providers.telephony.twilio_provider import TwilioTelephonyProvider

    twiml = TwilioTelephonyProvider.generate_media_stream_twiml(
        websocket_url=ws_url,
        call_sid=CallSid,
    )

    return Response(content=twiml, media_type="application/xml")


@router.post("/status")
async def handle_status_callback(
    request: Request,
    CallSid: str = Form(""),
    CallStatus: str = Form(""),
    CallDuration: str = Form("0"),
) -> Response:
    """Handle Twilio call status updates (ringing, answered, completed, etc.)."""
    logger.info("Call status: SID=%s status=%s duration=%s", CallSid, CallStatus, CallDuration)

    # TODO: Update call record in database
    # TODO: Handle 'completed' — trigger post-call if WS didn't already

    return Response(content="<Response/>", media_type="application/xml")


@ws_router.websocket("/ws/media-stream/{tenant_id}/{agent_id}")
async def media_stream_websocket(
    websocket: WebSocket,
    tenant_id: str,
    agent_id: str,
) -> None:
    """
    WebSocket endpoint for Twilio Media Streams.

    Twilio connects here after receiving <Connect><Stream> TwiML.
    Handles bidirectional audio streaming for the AI voice pipeline.
    """
    logger.info("Media stream WS connecting: tenant=%s agent=%s", tenant_id, agent_id)

    pipeline_config = _build_pipeline_config(tenant_id, agent_id)

    handler = TwilioMediaStreamHandler(websocket)
    await handler.handle(
        tenant_id=tenant_id,
        agent_id=agent_id,
        pipeline_config=pipeline_config,
        max_concurrent=5,  # TODO: Load from tenant config
        direction="inbound",
    )