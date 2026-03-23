"""
# TwilioMediaStreamHandler — WebSocket audio bridge
# Handles Twilio Media Streams protocol:
#   1. 'connected' event — WS handshake done
#   2. 'start' event — extract callSid, streamSid, create call session
#   3. 'media' event — decode base64 mulaw audio → feed to pipeline
#   4. 'stop' event — call ended, trigger post-call processing
#
# Outbound audio:
#   - Background task reads from audio queue
#   - Chunks audio into 20ms frames (160 bytes at 8kHz mulaw)
#   - Wraps in Twilio media message format (JSON + base64)
#   - Sends via WebSocket
#
# Post-call: queues Celery task for transcript storage + analytics
"""
"""
Twilio Media Streams WebSocket handler.

Handles the bidirectional WebSocket connection between Twilio and our server.
Incoming: μ-law/8kHz audio chunks from the caller → fed to VoicePipeline
Outgoing: synthesized audio from VoicePipeline → sent back to Twilio

Twilio Media Streams protocol:
- Messages are JSON with event types: connected, start, media, stop, mark
- Audio is base64-encoded μ-law at 8kHz mono in 20ms chunks
- Outbound audio is sent as base64-encoded media messages
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.voice.pipeline import PipelineConfig
from app.voice.session_manager import CallSession, call_manager

logger = logging.getLogger(__name__)


class TwilioMediaStreamHandler:
    """
    Handles a single Twilio Media Streams WebSocket connection.

    Lifecycle:
        1. Twilio opens WS after <Connect><Stream> TwiML
        2. We receive 'connected' and 'start' events with call metadata
        3. Audio flows bidirectionally via 'media' events
        4. Call ends with 'stop' event or WS disconnect
    """

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._stream_sid: str = ""
        self._call_sid: str = ""
        self._session: CallSession | None = None
        self._audio_out_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._running = False

    async def handle(
        self,
        tenant_id: str,
        agent_id: str,
        pipeline_config: PipelineConfig,
        max_concurrent: int = 5,
        direction: str = "inbound",
    ) -> None:
        """
        Main handler loop — process Twilio Media Stream messages.
        """
        await self._ws.accept()
        self._running = True

        # Start background task to send audio back to Twilio
        send_task = asyncio.create_task(self._audio_sender_loop())

        try:
            async for raw_message in self._ws.iter_text():
                if not self._running:
                    break

                try:
                    message = json.loads(raw_message)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from Twilio WS: %s", raw_message[:100])
                    continue

                event = message.get("event")

                if event == "connected":
                    logger.info("Twilio WS connected: protocol=%s", message.get("protocol"))

                elif event == "start":
                    await self._handle_start(
                        message, tenant_id, agent_id, pipeline_config,
                        max_concurrent, direction,
                    )

                elif event == "media":
                    await self._handle_media(message)

                elif event == "stop":
                    logger.info("Twilio stream stopped: call_sid=%s", self._call_sid)
                    break

                elif event == "mark":
                    # Mark events indicate playback position — useful for tracking
                    pass

        except WebSocketDisconnect:
            logger.info("Twilio WS disconnected: call_sid=%s", self._call_sid)
        except Exception as e:
            logger.error("Twilio WS error: %s", e)
        finally:
            self._running = False
            send_task.cancel()
            try:
                await send_task
            except asyncio.CancelledError:
                pass

            # End the call session
            if self._call_sid:
                session = await call_manager.end_session(self._call_sid)
                if session:
                    # Trigger post-call processing (transcription storage, analytics)
                    await self._trigger_post_call(session)

    async def _handle_start(
        self,
        message: dict[str, Any],
        tenant_id: str,
        agent_id: str,
        pipeline_config: PipelineConfig,
        max_concurrent: int,
        direction: str,
    ) -> None:
        """Handle the 'start' event — extract metadata and create call session."""
        start_data = message.get("start", {})
        self._stream_sid = start_data.get("streamSid", "")
        self._call_sid = start_data.get("callSid", "")

        # Extract custom parameters sent via TwiML <Parameter>
        custom_params = start_data.get("customParameters", {})
        if "call_sid" in custom_params:
            self._call_sid = custom_params["call_sid"]

        logger.info(
            "Media stream started: stream_sid=%s call_sid=%s",
            self._stream_sid, self._call_sid,
        )

        # Create call session with audio output callback
        async def ws_send_audio(audio_data: bytes) -> None:
            await self._audio_out_queue.put(audio_data)

        try:
            self._session = await call_manager.create_session(
                tenant_id=tenant_id,
                agent_id=agent_id,
                call_sid=self._call_sid or str(uuid.uuid4()),
                direction=direction,
                pipeline_config=pipeline_config,
                max_concurrent=max_concurrent,
                ws_send=ws_send_audio,
            )

            # Send greeting
            await self._session.pipeline.send_greeting()

        except Exception as e:
            logger.error("Failed to create call session: %s", e)
            await self._ws.close(code=1011, reason=str(e))

    async def _handle_media(self, message: dict[str, Any]) -> None:
        """Handle 'media' event — decode audio and feed to pipeline."""
        if not self._session:
            return

        media = message.get("media", {})
        payload = media.get("payload", "")

        if not payload:
            return

        # Decode base64 μ-law audio
        audio_bytes = base64.b64decode(payload)

        # Feed to pipeline for STT processing
        await self._session.pipeline.process_audio(audio_bytes, sample_rate=8000)

    async def _audio_sender_loop(self) -> None:
        """
        Background task: send synthesized audio back to Twilio.
        
        Audio is chunked into 20ms frames (160 bytes at 8kHz μ-law)
        and sent as base64-encoded media messages matching Twilio's protocol.
        """
        CHUNK_SIZE = 160  # 20ms at 8kHz

        try:
            while self._running:
                try:
                    audio_data = await asyncio.wait_for(
                        self._audio_out_queue.get(), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    continue

                if not self._stream_sid:
                    continue

                # Chunk audio into 20ms frames
                offset = 0
                while offset < len(audio_data):
                    chunk = audio_data[offset:offset + CHUNK_SIZE]
                    offset += CHUNK_SIZE

                    # Pad last chunk if necessary
                    if len(chunk) < CHUNK_SIZE:
                        chunk = chunk + b"\xff" * (CHUNK_SIZE - len(chunk))

                    # Send as Twilio media message
                    media_message = {
                        "event": "media",
                        "streamSid": self._stream_sid,
                        "media": {
                            "payload": base64.b64encode(chunk).decode("ascii"),
                        },
                    }

                    try:
                        await self._ws.send_text(json.dumps(media_message))
                    except Exception:
                        self._running = False
                        break

        except asyncio.CancelledError:
            pass

    async def _trigger_post_call(self, session: CallSession) -> None:
        """Trigger async post-call processing (Celery tasks)."""
        try:
            from app.tasks.call_tasks import process_post_call

            process_post_call.delay(
                call_id=session.call_id,
                call_sid=session.call_sid,
                tenant_id=session.tenant_id,
                agent_id=session.agent_id,
                direction=session.direction,
                transcript=session.transcript,
                metrics=session.metrics,
                duration_seconds=0,  # Calculated in the task
            )
            logger.info("Post-call processing queued: call_id=%s", session.call_id)
        except Exception as e:
            logger.error("Failed to queue post-call processing: %s", e)