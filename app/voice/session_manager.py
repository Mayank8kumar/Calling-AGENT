"""
# CallSessionManager — active call tracking
# Singleton that manages all active CallSession objects
# Key responsibilities:
#   create_session() — creates pipeline, enforces tenant concurrent limit
#   get_session(call_sid) — lookup active session
#   end_session(call_sid) — shutdown pipeline, return transcript/metrics
#   get_active_count(tenant_id) — for dashboard + rate limiting
#   get_active_sessions(tenant_id) — for monitoring
# Thread-safe via asyncio.Lock on session creation/deletion
"""

"""
Call Session Manager — tracks active calls, enforces tenant concurrency limits,
and bridges telephony WebSocket audio to the VoicePipeline.

Each active call gets a CallSession containing:
- The VoicePipeline instance
- Call metadata and state
- Audio I/O references
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.exceptions import ConcurrentCallLimitError
from app.voice.pipeline import PipelineConfig, TurnMetrics, VoicePipeline

logger = logging.getLogger(__name__)


@dataclass
class CallSession:
    """State for a single active call."""

    call_id: str
    tenant_id: str
    agent_id: str
    call_sid: str  # Telephony provider's external call ID
    direction: str  # inbound | outbound
    pipeline: VoicePipeline
    started_at: float = field(default_factory=time.time)

    # Transcript accumulated during the call
    transcript: list[dict[str, str]] = field(default_factory=list)
    metrics: list[dict[str, float]] = field(default_factory=list)

    # WebSocket reference for sending audio back to telephony
    ws_send: Callable[[bytes], Any] | None = None


class CallSessionManager:
    """
    Singleton manager for all active call sessions.

    Responsibilities:
    - Create and tear down call sessions
    - Enforce per-tenant concurrent call limits
    - Route audio between telephony WebSocket and VoicePipeline
    - Provide real-time call status for monitoring
    """

    def __init__(self) -> None:
        self._sessions: dict[str, CallSession] = {}  # call_sid → session
        self._tenant_call_counts: dict[str, int] = {}  # tenant_id → count
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        tenant_id: str,
        agent_id: str,
        call_sid: str,
        direction: str,
        pipeline_config: PipelineConfig,
        max_concurrent: int = 5,
        ws_send: Callable[[bytes], Any] | None = None,
    ) -> CallSession:
        """
        Create a new call session and initialize the voice pipeline.

        Raises ConcurrentCallLimitError if tenant has reached their limit.
        """
        async with self._lock:
            current_count = self._tenant_call_counts.get(tenant_id, 0)
            if current_count >= max_concurrent:
                raise ConcurrentCallLimitError(max_concurrent)

            call_id = str(uuid.uuid4())

            # Audio output callback — sends synthesized audio back through the WS
            async def on_audio_output(audio_data: bytes) -> None:
                if ws_send:
                    await ws_send(audio_data)

            # Transcript callback — accumulates for post-call storage
            session_transcript: list[dict[str, str]] = []

            async def on_transcript(role: str, text: str) -> None:
                session_transcript.append({
                    "role": role,
                    "content": text,
                    "timestamp": str(time.time()),
                })

            # Metrics callback
            session_metrics: list[dict[str, float]] = []

            async def on_turn_complete(metrics: TurnMetrics) -> None:
                session_metrics.append(metrics.to_dict())

            pipeline = VoicePipeline(
                call_id=call_id,
                config=pipeline_config,
                on_audio_output=on_audio_output,
                on_turn_complete=on_turn_complete,
                on_transcript=on_transcript,
            )

            session = CallSession(
                call_id=call_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                call_sid=call_sid,
                direction=direction,
                pipeline=pipeline,
                transcript=session_transcript,
                metrics=session_metrics,
                ws_send=ws_send,
            )

            self._sessions[call_sid] = session
            self._tenant_call_counts[tenant_id] = current_count + 1

            logger.info(
                "Session created: call_id=%s call_sid=%s tenant=%s direction=%s (concurrent: %d/%d)",
                call_id, call_sid, tenant_id, direction,
                current_count + 1, max_concurrent,
            )

        # Initialize pipeline (outside lock — may take time for provider connections)
        await pipeline.initialize()
        return session

    def get_session(self, call_sid: str) -> CallSession | None:
        """Get an active session by telephony call SID."""
        return self._sessions.get(call_sid)

    async def end_session(self, call_sid: str) -> CallSession | None:
        """
        End a call session: shut down pipeline, release resources, return session data.
        The returned session contains the full transcript and metrics for storage.
        """
        async with self._lock:
            session = self._sessions.pop(call_sid, None)
            if not session:
                logger.warning("No session found for call_sid=%s", call_sid)
                return None

            # Decrement tenant counter
            tenant_id = session.tenant_id
            count = self._tenant_call_counts.get(tenant_id, 1)
            if count <= 1:
                self._tenant_call_counts.pop(tenant_id, None)
            else:
                self._tenant_call_counts[tenant_id] = count - 1

        # Shutdown pipeline (outside lock)
        try:
            session.transcript = session.pipeline.get_transcript()
            session.metrics = session.pipeline.get_metrics()
            await session.pipeline.shutdown()
        except Exception as e:
            logger.error("Error shutting down pipeline for %s: %s", call_sid, e)

        duration = time.time() - session.started_at
        logger.info(
            "Session ended: call_id=%s call_sid=%s duration=%.1fs turns=%d",
            session.call_id, call_sid, duration, len(session.metrics),
        )

        return session

    def get_active_count(self, tenant_id: str | None = None) -> int:
        """Get count of active calls, optionally filtered by tenant."""
        if tenant_id:
            return self._tenant_call_counts.get(tenant_id, 0)
        return len(self._sessions)

    def get_active_sessions(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Get summary of all active sessions for monitoring."""
        sessions = []
        for sid, s in self._sessions.items():
            if tenant_id and s.tenant_id != tenant_id:
                continue
            sessions.append({
                "call_id": s.call_id,
                "call_sid": sid,
                "tenant_id": s.tenant_id,
                "direction": s.direction,
                "duration_seconds": time.time() - s.started_at,
                "turns": len(s.metrics),
            })
        return sessions


# Global singleton
call_manager = CallSessionManager()