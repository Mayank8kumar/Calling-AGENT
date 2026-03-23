"""
# Twilio telephony — primary call management
# Outbound: initiate_call() → REST API, with AMD (voicemail detection)
# Inbound: answer_call() → generates TwiML with <Connect><Stream>
# Call control: end_call(), transfer_call(), play_audio(), send_dtmf()
# TwiML helper: generate_media_stream_twiml()
#   1. Optional consent message via <Say>
#   2. <Connect><Stream url="wss://..."> for bidirectional audio
# The TwiML tells Twilio to open a WebSocket to our server,
# which is handled by media_stream.py
# Registered as: registry.register_telephony("twilio", ...)
"""

"""
Twilio telephony provider — PSTN call management + Media Streams WebSocket.

Handles:
- Outbound call initiation via REST API
- Inbound call answering with TwiML → Media Stream WebSocket
- Call transfer (warm/cold)
- DTMF, recording controls
"""

from __future__ import annotations

import logging
from typing import Any

from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import Connect, VoiceResponse

from app.voice.providers.base import TelephonyProvider, registry

logger = logging.getLogger(__name__)


class TwilioTelephonyProvider(TelephonyProvider):
    provider_name = "twilio"

    def __init__(self, account_sid: str, auth_token: str, phone_number: str = "") -> None:
        self._client = TwilioClient(account_sid, auth_token)
        self._phone_number = phone_number
        self._account_sid = account_sid

    async def initiate_call(
        self,
        to_number: str,
        from_number: str,
        webhook_url: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Start an outbound call via Twilio REST API.
        The webhook_url receives the TwiML response when the call connects.
        Returns the Twilio Call SID.
        """
        from_number = from_number or self._phone_number
        if not from_number:
            raise ValueError("from_number is required for outbound calls")

        call_params: dict[str, Any] = {
            "to": to_number,
            "from_": from_number,
            "url": webhook_url,
            "method": "POST",
            "status_callback": webhook_url.rstrip("/") + "/status",
            "status_callback_method": "POST",
            "status_callback_event": ["initiated", "ringing", "answered", "completed"],
            "machine_detection": "Enable",  # AMD for voicemail detection
            "machine_detection_timeout": 5,
        }

        if metadata:
            # Store metadata as SIP headers or custom parameters
            call_params["sip_headers"] = {
                f"X-{k}": str(v) for k, v in metadata.items()
            }

        # Note: twilio-python client is synchronous — run in executor for async
        import asyncio

        loop = asyncio.get_event_loop()
        call = await loop.run_in_executor(
            None,
            lambda: self._client.calls.create(**call_params),
        )

        logger.info("Twilio outbound call initiated: SID=%s to=%s", call.sid, to_number)
        return call.sid

    async def answer_call(
        self,
        call_sid: str,
        websocket_url: str,
    ) -> dict[str, Any]:
        """
        Generate TwiML response that connects the call to a bidirectional
        Media Stream WebSocket for real-time audio processing.

        Returns dict with 'twiml' key containing the XML response.
        """
        response = VoiceResponse()

        # Connect to bidirectional Media Stream
        connect = Connect()
        stream = connect.stream(url=websocket_url)
        stream.parameter(name="call_sid", value=call_sid)
        response.append(connect)

        twiml_str = str(response)
        logger.debug("Generated TwiML for call %s: %s", call_sid, twiml_str[:200])

        return {"twiml": twiml_str, "content_type": "application/xml"}

    async def end_call(self, call_sid: str) -> None:
        """Hang up a call by updating its status to 'completed'."""
        import asyncio

        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.calls(call_sid).update(status="completed"),
            )
            logger.info("Call ended: SID=%s", call_sid)
        except Exception as e:
            logger.error("Failed to end call %s: %s", call_sid, e)
            raise

    async def transfer_call(
        self,
        call_sid: str,
        to_number: str,
        announce_message: str | None = None,
    ) -> None:
        """
        Transfer call to a human agent.
        Uses TwiML update to redirect the call with an optional announcement.
        """
        import asyncio

        response = VoiceResponse()
        if announce_message:
            response.say(announce_message, voice="Polly.Joanna")
        response.dial(to_number, timeout=30, action="/api/v1/webhooks/twilio/transfer-complete")

        twiml_str = str(response)

        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.calls(call_sid).update(twiml=twiml_str),
        )
        logger.info("Call %s transferred to %s", call_sid, to_number)

    async def play_audio(self, call_sid: str, audio_url: str) -> None:
        """Play a pre-recorded audio file into the call."""
        import asyncio

        response = VoiceResponse()
        response.play(audio_url)

        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.calls(call_sid).update(twiml=str(response)),
        )

    async def send_dtmf(self, call_sid: str, digits: str) -> None:
        """Send DTMF tones into the call."""
        import asyncio

        response = VoiceResponse()
        response.play(digits=digits)

        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.calls(call_sid).update(twiml=str(response)),
        )

    @staticmethod
    def generate_media_stream_twiml(
        websocket_url: str,
        call_sid: str,
        consent_message: str | None = None,
    ) -> str:
        """
        Helper to generate TwiML that:
        1. Optionally plays a recording consent message
        2. Opens a bidirectional Media Stream WebSocket

        Used by the webhook handler for both inbound and outbound calls.
        """
        response = VoiceResponse()

        if consent_message:
            response.say(consent_message, voice="Polly.Joanna")
            response.pause(length=1)

        connect = Connect()
        stream = connect.stream(url=websocket_url)
        stream.parameter(name="call_sid", value=call_sid)
        response.append(connect)

        return str(response)


# Register with the provider registry
registry.register_telephony("twilio", TwilioTelephonyProvider)