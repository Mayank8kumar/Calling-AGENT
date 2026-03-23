"""
# Plivo telephony — India cost optimization
# Key advantage: FREE audio streaming for India calls (vs $0.004/min on Twilio)
# Same interface as Twilio provider:
#   initiate_call() — Plivo REST API with machine_detection
#   answer_call() — generates Plivo XML with <Stream> element
#   end_call(), transfer_call(), play_audio(), send_dtmf()
# Differences from Twilio:
#   - Uses Plivo XML instead of TwiML
#   - Different WebSocket audio format
#   - Indian phone number support via Plivo's carrier relationships
# Use for: India-only traffic to save ~60% on telephony costs
# Registered as: registry.register_telephony("plivo", ...)
"""

"""
Plivo telephony provider — cost-optimized for India traffic.

Key advantage: Free audio streaming for India calls.
Supports bidirectional audio streaming via Plivo's XML + WebSocket.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import plivo

from app.voice.providers.base import TelephonyProvider, registry

logger = logging.getLogger(__name__)


class PlivoTelephonyProvider(TelephonyProvider):
    provider_name = "plivo"

    def __init__(self, auth_id: str, auth_token: str, phone_number: str = "") -> None:
        self._client = plivo.RestClient(auth_id, auth_token)
        self._phone_number = phone_number

    async def initiate_call(
        self,
        to_number: str,
        from_number: str,
        webhook_url: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        from_number = from_number or self._phone_number
        loop = asyncio.get_event_loop()

        response = await loop.run_in_executor(
            None,
            lambda: self._client.calls.create(
                from_=from_number,
                to_=to_number,
                answer_url=webhook_url,
                answer_method="POST",
                hangup_url=webhook_url.rstrip("/") + "/hangup",
                hangup_method="POST",
                machine_detection="true",
                machine_detection_time=5000,
            ),
        )

        call_uuid = response.request_uuid if hasattr(response, "request_uuid") else str(response)
        logger.info("Plivo outbound call initiated: UUID=%s to=%s", call_uuid, to_number)
        return call_uuid

    async def answer_call(
        self, call_sid: str, websocket_url: str
    ) -> dict[str, Any]:
        """Generate Plivo XML that connects to audio streaming WebSocket."""
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true"
            contentType="audio/x-mulaw;rate=8000"
            statusCallbackUrl="{websocket_url.rstrip('/')}/status"
            statusCallbackMethod="POST">
        {websocket_url}
    </Stream>
</Response>"""
        return {"xml": xml, "content_type": "application/xml"}

    async def end_call(self, call_sid: str) -> None:
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None, lambda: self._client.calls.hangup(call_sid)
            )
            logger.info("Plivo call ended: UUID=%s", call_sid)
        except Exception as e:
            logger.error("Failed to end Plivo call %s: %s", call_sid, e)
            raise

    async def transfer_call(
        self, call_sid: str, to_number: str, announce_message: str | None = None
    ) -> None:
        loop = asyncio.get_event_loop()
        xml = "<Response>"
        if announce_message:
            xml += f"<Speak>{announce_message}</Speak>"
        xml += f'<Dial><Number>{to_number}</Number></Dial></Response>'

        await loop.run_in_executor(
            None,
            lambda: self._client.calls.update(call_sid, legs="aleg", aleg_url_method="POST"),
        )
        logger.info("Plivo call %s transferred to %s", call_sid, to_number)

    async def play_audio(self, call_sid: str, audio_url: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: self._client.calls.play(call_sid, [audio_url])
        )

    async def send_dtmf(self, call_sid: str, digits: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: self._client.calls.send_digits(call_sid, digits)
        )


registry.register_telephony("plivo", PlivoTelephonyProvider)