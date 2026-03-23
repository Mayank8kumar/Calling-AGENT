"""
# VoicePipeline — THE CORE ENGINE
# Manages STT → LLM → TTS for a single call with streaming overlap:
#
#   User speaks → Deepgram transcribes (streaming)
#       ↓ (final transcript)
#   OpenAI generates response (streaming tokens)
#       ↓ (tokens arrive one by one)
#   Cartesia synthesizes audio (streaming, concurrent with LLM)
#       ↓ (audio chunks)
#   Audio sent back to caller via WebSocket
#
# Key features:
#   - Streaming overlap: LLM+TTS run concurrently via asyncio tasks
#   - Barge-in: if user speaks during TTS, cancel audio and re-listen
#   - Silence detection: prompt user after N seconds of silence
#   - Turn metrics: tracks STT/LLM/TTS latency per turn
#   - Conversation context: maintains message history within the call
#
# Lifecycle: initialize() → process_audio() per chunk → shutdown()
"""

"""
Voice Pipeline Orchestrator — the core engine that chains STT → LLM → TTS
with streaming overlap for minimal latency.

Architecture:
    Telephony audio → STT (streaming) → LLM (streaming) → TTS (streaming) → Telephony audio
    
All three AI stages run concurrently via asyncio tasks. LLM starts generating
as soon as STT produces a final transcript. TTS starts synthesizing as soon as
LLM yields its first token. This overlap is the key to achieving <500ms perceived latency.

Each call gets its own VoicePipeline instance managing:
- Conversation context (message history)
- Provider instances (STT, LLM, TTS)
- Barge-in handling (cancel TTS when user interrupts)
- Turn metrics collection
- Silence detection and prompting
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from app.voice.providers.base import (
    AudioChunk,
    LLMMessage,
    LLMProvider,
    STTProvider,
    TTSAudioChunk,
    TTSProvider,
    TranscriptSegment,
    registry,
)

logger = logging.getLogger(__name__)


@dataclass
class TurnMetrics:
    """Latency metrics for a single conversational turn."""
    turn_id: str = ""
    stt_start_ms: float = 0
    stt_end_ms: float = 0
    llm_start_ms: float = 0
    llm_first_token_ms: float = 0
    llm_end_ms: float = 0
    tts_start_ms: float = 0
    tts_first_audio_ms: float = 0
    tts_end_ms: float = 0
    total_ms: float = 0

    @property
    def stt_latency_ms(self) -> float:
        return self.stt_end_ms - self.stt_start_ms

    @property
    def llm_ttft_ms(self) -> float:
        return self.llm_first_token_ms - self.llm_start_ms

    @property
    def tts_ttfb_ms(self) -> float:
        return self.tts_first_audio_ms - self.tts_start_ms

    def to_dict(self) -> dict[str, float]:
        return {
            "turn_id": self.turn_id,
            "stt_ms": self.stt_latency_ms,
            "llm_ttft_ms": self.llm_ttft_ms,
            "tts_ttfb_ms": self.tts_ttfb_ms,
            "total_ms": self.total_ms,
        }


@dataclass
class PipelineConfig:
    """Configuration for a pipeline instance, resolved from agent + tenant settings."""
    # Provider names
    stt_provider: str = "deepgram"
    llm_provider: str = "openai"
    tts_provider: str = "cartesia"

    # Provider-specific configs
    stt_config: dict[str, Any] = field(default_factory=dict)
    llm_config: dict[str, Any] = field(default_factory=dict)
    tts_config: dict[str, Any] = field(default_factory=dict)

    # Agent behavior
    system_prompt: str = "You are a helpful voice assistant. Keep responses concise and conversational — under 2 sentences."
    greeting_message: str = "Hello! How can I help you today?"
    language: str = "en"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 200
    tools: list[dict[str, Any]] = field(default_factory=list)

    # Behavior tuning
    max_silence_seconds: int = 10
    max_call_duration_seconds: int = 600
    enable_barge_in: bool = True


class VoicePipeline:
    """
    Manages the complete voice AI pipeline for a single call.

    Lifecycle:
        1. initialize() — connect to all providers
        2. process_audio() — called per audio chunk from telephony WebSocket
        3. Internal loop: STT transcripts → LLM → TTS → audio output callback
        4. shutdown() — clean up all connections
    """

    def __init__(
        self,
        call_id: str,
        config: PipelineConfig,
        on_audio_output: Callable[[bytes], Any],
        on_turn_complete: Callable[[TurnMetrics], Any] | None = None,
        on_transcript: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.call_id = call_id
        self.config = config
        self._on_audio_output = on_audio_output
        self._on_turn_complete = on_turn_complete
        self._on_transcript = on_transcript  # (role, text) callback

        # Provider instances
        self._stt: STTProvider | None = None
        self._llm: LLMProvider | None = None
        self._tts: TTSProvider | None = None

        # Conversation state
        self._messages: list[LLMMessage] = []
        self._current_user_text: str = ""
        self._is_speaking = False  # True while TTS is outputting audio
        self._is_processing = False
        self._turn_count = 0
        self._all_metrics: list[TurnMetrics] = []

        # Control
        self._running = False
        self._transcript_task: asyncio.Task | None = None
        self._silence_task: asyncio.Task | None = None
        self._barge_in_event = asyncio.Event()

    async def initialize(self) -> None:
        """Connect to all AI providers and prepare the pipeline."""
        logger.info("[%s] Initializing voice pipeline", self.call_id)

        # Instantiate providers from registry
        stt_cls = registry.get_stt(self.config.stt_provider)
        self._stt = stt_cls()
        await self._stt.connect(self.config.stt_config)

        # LLM — instantiated with API key from config
        llm_cls = registry.get_llm(self.config.llm_provider)
        self._llm = llm_cls(
            api_key=self.config.llm_config.get("api_key", ""),
            model=self.config.llm_config.get("model", "gpt-4.1-mini"),
        )

        # TTS
        tts_cls = registry.get_tts(self.config.tts_provider)
        self._tts = tts_cls()
        await self._tts.connect(self.config.tts_config)

        # Initialize conversation with system prompt
        self._messages = [
            LLMMessage(role="system", content=self.config.system_prompt)
        ]

        self._running = True

        # Start background transcript processing loop
        self._transcript_task = asyncio.create_task(self._transcript_loop())

        logger.info("[%s] Pipeline initialized: STT=%s LLM=%s TTS=%s",
                     self.call_id, self.config.stt_provider,
                     self.config.llm_provider, self.config.tts_provider)

    async def process_audio(self, audio_data: bytes, sample_rate: int = 8000) -> None:
        """
        Called per audio chunk from the telephony WebSocket.
        Feeds audio to STT for real-time transcription.
        """
        if not self._running or not self._stt:
            return

        chunk = AudioChunk(
            data=audio_data,
            sample_rate=sample_rate,
            encoding="mulaw",
        )
        await self._stt.stream_audio(chunk)

        # Handle barge-in: if user speaks while agent is outputting audio
        if self.config.enable_barge_in and self._is_speaking:
            await self._handle_barge_in()

    async def send_greeting(self) -> None:
        """Synthesize and send the greeting message at call start."""
        if not self._tts or not self.config.greeting_message:
            return

        logger.info("[%s] Sending greeting", self.call_id)

        try:
            audio = await self._tts.synthesize(self.config.greeting_message)
            if audio:
                await self._on_audio_output(audio)

            # Add greeting to conversation history
            self._messages.append(
                LLMMessage(role="assistant", content=self.config.greeting_message)
            )
            if self._on_transcript:
                await self._on_transcript("assistant", self.config.greeting_message)
        except Exception as e:
            logger.error("[%s] Greeting synthesis failed: %s", self.call_id, e)

    async def _transcript_loop(self) -> None:
        """
        Background task: consume STT transcripts and trigger LLM → TTS pipeline.

        Waits for final transcripts (end of user utterance), then kicks off
        the streaming response generation.
        """
        if not self._stt:
            return

        try:
            async for segment in self._stt.get_transcripts():
                if not self._running:
                    break

                if segment.text:
                    self._current_user_text += (" " if self._current_user_text else "") + segment.text

                    # Reset silence timer on any speech
                    self._reset_silence_timer()

                if segment.is_final and self._current_user_text.strip():
                    user_text = self._current_user_text.strip()
                    self._current_user_text = ""

                    logger.info("[%s] User said: %s", self.call_id, user_text[:100])

                    if self._on_transcript:
                        await self._on_transcript("user", user_text)

                    # Process the turn: LLM → TTS streaming
                    await self._process_turn(user_text)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("[%s] Transcript loop error: %s", self.call_id, e)

    async def _process_turn(self, user_text: str) -> None:
        """
        Process one conversational turn with streaming overlap:
        1. Add user message to context
        2. Stream LLM response
        3. Simultaneously stream LLM tokens → TTS → audio output
        """
        if self._is_processing:
            logger.warning("[%s] Overlapping turn — skipping", self.call_id)
            return

        self._is_processing = True
        self._turn_count += 1
        metrics = TurnMetrics(turn_id=f"turn_{self._turn_count}")

        try:
            # Add user message to conversation
            self._messages.append(LLMMessage(role="user", content=user_text))

            # --- LLM streaming ---
            metrics.llm_start_ms = time.monotonic() * 1000

            if not self._llm or not self._tts:
                return

            # Create an async generator bridge: LLM tokens → TTS input
            llm_token_queue: asyncio.Queue[str | None] = asyncio.Queue()
            full_response_parts: list[str] = []

            async def llm_token_producer() -> None:
                """Produce LLM tokens into the queue."""
                first_token = True
                try:
                    async for token in self._llm.generate_stream(
                        messages=self._messages,
                        tools=self.config.tools or None,
                        temperature=self.config.llm_temperature,
                        max_tokens=self.config.llm_max_tokens,
                    ):
                        if first_token:
                            metrics.llm_first_token_ms = time.monotonic() * 1000
                            first_token = True
                        full_response_parts.append(token)
                        await llm_token_queue.put(token)
                except Exception as e:
                    logger.error("[%s] LLM generation error: %s", self.call_id, e)
                finally:
                    await llm_token_queue.put(None)  # Signal end
                    metrics.llm_end_ms = time.monotonic() * 1000

            async def llm_token_stream() -> AsyncIterator[str]:
                """Consume from queue as an async iterator for TTS."""
                while True:
                    token = await llm_token_queue.get()
                    if token is None:
                        break
                    yield token

            # --- Start concurrent LLM + TTS streaming ---
            metrics.tts_start_ms = time.monotonic() * 1000
            self._is_speaking = True
            self._barge_in_event.clear()

            # Start LLM producer task
            llm_task = asyncio.create_task(llm_token_producer())

            # Stream TTS audio as LLM tokens arrive
            first_audio = True
            try:
                async for audio_chunk in self._tts.synthesize_stream(llm_token_stream()):
                    # Check for barge-in
                    if self._barge_in_event.is_set():
                        logger.info("[%s] Barge-in — stopping TTS output", self.call_id)
                        await self._tts.cancel()
                        break

                    if first_audio and audio_chunk.data:
                        metrics.tts_first_audio_ms = time.monotonic() * 1000
                        first_audio = False

                    if audio_chunk.data:
                        await self._on_audio_output(audio_chunk.data)
            except Exception as e:
                logger.error("[%s] TTS streaming error: %s", self.call_id, e)

            # Wait for LLM to finish
            await llm_task

            self._is_speaking = False
            metrics.tts_end_ms = time.monotonic() * 1000
            metrics.total_ms = metrics.tts_end_ms - metrics.llm_start_ms

            # Store assistant response in conversation history
            full_response = "".join(full_response_parts)
            if full_response:
                self._messages.append(
                    LLMMessage(role="assistant", content=full_response)
                )
                if self._on_transcript:
                    await self._on_transcript("assistant", full_response)

            # Report metrics
            self._all_metrics.append(metrics)
            logger.info(
                "[%s] Turn %d complete: LLM TTFT=%.0fms TTS TTFB=%.0fms Total=%.0fms",
                self.call_id, self._turn_count,
                metrics.llm_ttft_ms, metrics.tts_ttfb_ms, metrics.total_ms,
            )

            if self._on_turn_complete:
                await self._on_turn_complete(metrics)

        except Exception as e:
            logger.error("[%s] Turn processing error: %s", self.call_id, e)
        finally:
            self._is_processing = False

    async def _handle_barge_in(self) -> None:
        """Handle user interrupting the agent (barge-in)."""
        if not self._is_speaking:
            return
        logger.info("[%s] Barge-in detected", self.call_id)
        self._barge_in_event.set()

    def _reset_silence_timer(self) -> None:
        """Reset the silence detection timer."""
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()
        self._silence_task = asyncio.create_task(self._silence_watchdog())

    async def _silence_watchdog(self) -> None:
        """Prompt the user if silence exceeds the configured threshold."""
        try:
            await asyncio.sleep(self.config.max_silence_seconds)
            if self._running and not self._is_processing:
                logger.info("[%s] Silence detected — prompting user", self.call_id)
                await self._process_turn(
                    "[System: The user has been silent. Gently ask if they're still there or need help.]"
                )
        except asyncio.CancelledError:
            pass

    def get_metrics(self) -> list[dict[str, float]]:
        """Return all turn metrics for post-call analysis."""
        return [m.to_dict() for m in self._all_metrics]

    def get_transcript(self) -> list[dict[str, str]]:
        """Return conversation history excluding system prompt."""
        return [
            {"role": m.role, "content": m.content}
            for m in self._messages
            if m.role != "system"
        ]

    async def shutdown(self) -> None:
        """Clean up all provider connections."""
        logger.info("[%s] Shutting down pipeline", self.call_id)
        self._running = False

        if self._transcript_task and not self._transcript_task.done():
            self._transcript_task.cancel()
            try:
                await self._transcript_task
            except asyncio.CancelledError:
                pass

        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()

        if self._stt:
            await self._stt.close()
        if self._tts:
            await self._tts.close()
        if self._llm:
            await self._llm.close()

        logger.info("[%s] Pipeline shut down. Turns: %d", self.call_id, self._turn_count)