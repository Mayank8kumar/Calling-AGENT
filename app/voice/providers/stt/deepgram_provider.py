"""
# Deepgram STT — real-time streaming transcription
# Uses Deepgram Nova-3 model via WebSocket
# Key config:
#   model=nova-3, language=multi (Hindi-English code-switching)
#   endpointing=300ms (Flux model for semantic end-of-turn)
#   interim_results=True, vad_events=True, smart_format=True
# How it works:
#   connect() — opens live WebSocket to Deepgram
#   stream_audio(chunk) — sends raw mulaw bytes
#   get_transcripts() — async generator yielding TranscriptSegment
#   Events: Transcript (interim/final), UtteranceEnd (end of speech), Error
# Registered as: registry.register_stt("deepgram", ...)
"""
"""
Deepgram STT provider — real-time streaming transcription via WebSocket.

Supports:
- Nova-3 model with sub-300ms streaming latency
- Hindi-English code-switching (language=multi)
- Endpointing (Flux model) for semantic end-of-turn detection
- Interim results for early LLM triggering
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveOptions,
    LiveTranscriptionEvents,
)

from app.voice.providers.base import AudioChunk, STTProvider, TranscriptSegment, registry

logger = logging.getLogger(__name__)


class DeepgramSTTProvider(STTProvider):
    provider_name = "deepgram"

    def __init__(self) -> None:
        self._client: DeepgramClient | None = None
        self._connection: Any = None
        self._transcript_queue: asyncio.Queue[TranscriptSegment] = asyncio.Queue()
        self._is_connected = False

    async def connect(self, config: dict[str, Any]) -> None:
        """
        Open a live transcription WebSocket.

        Config keys:
            api_key: str (required)
            model: str (default: nova-3)
            language: str (default: multi)
            sample_rate: int (default: 8000)
            encoding: str (default: mulaw)
            channels: int (default: 1)
            endpointing: int (default: 300, ms — use Flux model for semantic)
            interim_results: bool (default: True)
            utterance_end_ms: int (default: 1500)
            vad_events: bool (default: True)
            smart_format: bool (default: True)
        """
        api_key = config.get("api_key", "")
        if not api_key:
            raise ValueError("Deepgram API key is required")

        client_config = DeepgramClientOptions(
            options={"keepalive": "true"},
        )
        self._client = DeepgramClient(api_key, config=client_config)
        self._connection = self._client.listen.asynclive.v("1")

        # Register event handlers
        self._connection.on(LiveTranscriptionEvents.Transcript, self._on_transcript)
        self._connection.on(LiveTranscriptionEvents.UtteranceEnd, self._on_utterance_end)
        self._connection.on(LiveTranscriptionEvents.Error, self._on_error)

        options = LiveOptions(
            model=config.get("model", "nova-3"),
            language=config.get("language", "multi"),
            sample_rate=config.get("sample_rate", 8000),
            encoding=config.get("encoding", "mulaw"),
            channels=config.get("channels", 1),
            endpointing=config.get("endpointing", 300),
            interim_results=config.get("interim_results", True),
            utterance_end_ms=config.get("utterance_end_ms", 1500),
            vad_events=config.get("vad_events", True),
            smart_format=config.get("smart_format", True),
            punctuate=True,
            filler_words=False,
        )

        started = await self._connection.start(options)
        if not started:
            raise ConnectionError("Failed to start Deepgram live transcription")

        self._is_connected = True
        logger.info("Deepgram STT connected: model=%s lang=%s", options.model, options.language)

    async def _on_transcript(self, _self: Any, result: Any, **kwargs: Any) -> None:
        """Handle incoming transcript events."""
        try:
            transcript = result.channel.alternatives[0].transcript
            if not transcript:
                return

            segment = TranscriptSegment(
                text=transcript,
                is_final=result.is_final,
                confidence=result.channel.alternatives[0].confidence,
                language=result.channel.alternatives[0].languages[0]
                if hasattr(result.channel.alternatives[0], "languages")
                and result.channel.alternatives[0].languages
                else None,
                start_time=result.start,
                end_time=result.start + result.duration,
            )
            await self._transcript_queue.put(segment)
        except (IndexError, AttributeError) as e:
            logger.warning("Failed to parse Deepgram transcript: %s", e)

    async def _on_utterance_end(self, _self: Any, result: Any, **kwargs: Any) -> None:
        """Signal end of utterance — important for turn-taking."""
        segment = TranscriptSegment(
            text="",
            is_final=True,
            confidence=1.0,
        )
        await self._transcript_queue.put(segment)

    async def _on_error(self, _self: Any, error: Any, **kwargs: Any) -> None:
        logger.error("Deepgram STT error: %s", error)

    async def stream_audio(self, chunk: AudioChunk) -> None:
        """Send raw audio bytes to Deepgram for real-time transcription."""
        if not self._is_connected or not self._connection:
            return
        try:
            await self._connection.send(chunk.data)
        except Exception as e:
            logger.error("Failed to send audio to Deepgram: %s", e)

    async def get_transcripts(self) -> AsyncIterator[TranscriptSegment]:
        """Yield transcript segments as they arrive."""
        while self._is_connected:
            try:
                segment = await asyncio.wait_for(self._transcript_queue.get(), timeout=0.1)
                yield segment
            except asyncio.TimeoutError:
                continue

    async def close(self) -> None:
        """Cleanly close the WebSocket connection."""
        self._is_connected = False
        if self._connection:
            try:
                await self._connection.finish()
            except Exception as e:
                logger.warning("Error closing Deepgram connection: %s", e)
            self._connection = None
        self._client = None

    async def reset(self) -> None:
        """Reset for a new utterance (called on barge-in)."""
        # Clear pending transcripts
        while not self._transcript_queue.empty():
            try:
                self._transcript_queue.get_nowait()
            except asyncio.QueueEmpty:
                break


# Register with the provider registry
registry.register_stt("deepgram", DeepgramSTTProvider)