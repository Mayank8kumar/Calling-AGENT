"""
# Abstract provider interfaces + registry
# Data classes:
#   AudioChunk — raw audio bytes with sample rate, encoding
#   TranscriptSegment — STT output (text, is_final, confidence, language)
#   LLMMessage — conversation message (role, content, tool_calls)
#   LLMResponse — complete LLM response (text, finish_reason, tool_calls, usage)
#   TTSAudioChunk — synthesized audio chunk (data, sample_rate, is_final)
#   CallEvent — telephony events (call.started, call.ended, etc.)
#
# Abstract Base Classes:
#   STTProvider — connect(), stream_audio(), get_transcripts(), close()
#   LLMProvider — generate(), generate_stream(), close()
#   TTSProvider — connect(), synthesize_stream(), synthesize(), cancel(), close()
#   TelephonyProvider — initiate_call(), answer_call(), end_call(), transfer_call()
#
# ProviderRegistry — singleton that maps provider names to classes
#   registry.register_stt("deepgram", DeepgramSTTProvider)
#   registry.get_stt("deepgram")  → returns the class
"""
# File: voice-agent-platform/app/voice/providers/base.py
"""
Abstract interfaces for the voice AI pipeline.

These ABCs define the contract every provider must implement.
The pipeline orchestrator (pipeline.py) only interacts with these interfaces,
making providers fully swappable at runtime per-tenant.

Pipeline flow:
    User Voice → Telephony → STT → LLM → TTS → Telephony → User
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable


# ---------------------------------------------------------------------------
# Common data structures
# ---------------------------------------------------------------------------

@dataclass
class AudioChunk:
    """Raw audio data flowing through the pipeline."""

    data: bytes
    sample_rate: int = 8000
    channels: int = 1
    encoding: str = "mulaw"  # mulaw | pcm_s16le | pcm_f32le
    timestamp_ms: float = 0.0


@dataclass
class TranscriptSegment:
    """A segment of transcribed speech from STT."""

    text: str
    is_final: bool = False
    confidence: float = 0.0
    language: str | None = None
    start_time: float = 0.0
    end_time: float = 0.0
    speaker: str | None = None  # For diarization


@dataclass
class LLMMessage:
    """A message in the LLM conversation context."""

    role: str  # system | user | assistant | tool
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    timestamp_ms: float = 0.0


@dataclass
class LLMResponse:
    """Complete or partial LLM response."""

    text: str
    finish_reason: str | None = None  # stop | tool_calls | length
    tool_calls: list[dict[str, Any]] | None = None
    usage: dict[str, int] | None = None  # {prompt_tokens, completion_tokens}


@dataclass
class TTSAudioChunk:
    """A chunk of synthesized audio from TTS."""

    data: bytes
    sample_rate: int = 24000
    encoding: str = "pcm_s16le"
    is_final: bool = False


@dataclass
class CallEvent:
    """Events emitted by the telephony provider."""

    event_type: str  # call.started | call.ended | call.dtmf | call.error
    call_sid: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp_ms: float = 0.0


# ---------------------------------------------------------------------------
# Provider interfaces
# ---------------------------------------------------------------------------

class STTProvider(ABC):
    """
    Speech-to-Text provider interface.
    Must support real-time streaming transcription via WebSocket.
    """

    provider_name: str = "base_stt"

    @abstractmethod
    async def connect(self, config: dict[str, Any]) -> None:
        """Initialize connection to STT service (e.g., open WebSocket)."""
        ...

    @abstractmethod
    async def stream_audio(self, chunk: AudioChunk) -> None:
        """Send an audio chunk to the STT engine for processing."""
        ...

    @abstractmethod
    async def get_transcripts(self) -> AsyncIterator[TranscriptSegment]:
        """Yield transcript segments as they become available."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Cleanly close the STT connection."""
        ...

    async def reset(self) -> None:
        """Reset for a new utterance (e.g., after barge-in)."""
        await self.close()


class LLMProvider(ABC):
    """
    Large Language Model provider interface.
    Must support streaming responses for low-latency TTS feeding.
    """

    provider_name: str = "base_llm"

    @abstractmethod
    async def generate(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 200,
    ) -> LLMResponse:
        """Generate a complete response (non-streaming)."""
        ...

    @abstractmethod
    async def generate_stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 200,
    ) -> AsyncIterator[str]:
        """Stream response tokens one-by-one for real-time TTS feeding."""
        ...

    async def close(self) -> None:
        """Release any resources."""
        pass


class TTSProvider(ABC):
    """
    Text-to-Speech provider interface.
    Must support text streaming input (feeding tokens as they arrive from LLM)
    and audio streaming output (yielding audio chunks progressively).
    """

    provider_name: str = "base_tts"

    @abstractmethod
    async def connect(self, config: dict[str, Any]) -> None:
        """Initialize connection (e.g., open WebSocket to TTS service)."""
        ...

    @abstractmethod
    async def synthesize_stream(
        self, text_stream: AsyncIterator[str]
    ) -> AsyncIterator[TTSAudioChunk]:
        """
        Accept a stream of text tokens, yield audio chunks as they're synthesized.
        This is the key streaming interface that enables pipeline parallelism.
        """
        ...

    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """Synthesize complete text to audio (for short/cached responses)."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Cleanly close the TTS connection."""
        ...

    async def cancel(self) -> None:
        """Cancel current synthesis (e.g., on barge-in). Override if supported."""
        pass


class TelephonyProvider(ABC):
    """
    Telephony provider interface.
    Handles PSTN/SIP call management and audio streaming via WebSocket.
    """

    provider_name: str = "base_telephony"

    @abstractmethod
    async def initiate_call(
        self,
        to_number: str,
        from_number: str,
        webhook_url: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Start an outbound call. Returns provider call SID."""
        ...

    @abstractmethod
    async def answer_call(
        self,
        call_sid: str,
        websocket_url: str,
    ) -> dict[str, Any]:
        """
        Generate response instructions to answer an inbound call
        and connect to a media WebSocket.
        Returns provider-specific response (TwiML, XML, etc.).
        """
        ...

    @abstractmethod
    async def end_call(self, call_sid: str) -> None:
        """Hang up a call."""
        ...

    @abstractmethod
    async def transfer_call(
        self,
        call_sid: str,
        to_number: str,
        announce_message: str | None = None,
    ) -> None:
        """Transfer call to a human agent."""
        ...

    @abstractmethod
    async def play_audio(self, call_sid: str, audio_url: str) -> None:
        """Play a pre-recorded audio file into the call."""
        ...

    @abstractmethod
    async def send_dtmf(self, call_sid: str, digits: str) -> None:
        """Send DTMF tones."""
        ...

    async def close(self) -> None:
        """Cleanup provider resources."""
        pass


# ---------------------------------------------------------------------------
# Provider registry — runtime resolution by name
# ---------------------------------------------------------------------------

class ProviderRegistry:
    """
    Central registry mapping provider names to their implementations.
    Used by the pipeline to resolve tenant-configured providers at runtime.
    """

    def __init__(self) -> None:
        self._stt: dict[str, type[STTProvider]] = {}
        self._llm: dict[str, type[LLMProvider]] = {}
        self._tts: dict[str, type[TTSProvider]] = {}
        self._telephony: dict[str, type[TelephonyProvider]] = {}

    def register_stt(self, name: str, cls: type[STTProvider]) -> None:
        self._stt[name] = cls

    def register_llm(self, name: str, cls: type[LLMProvider]) -> None:
        self._llm[name] = cls

    def register_tts(self, name: str, cls: type[TTSProvider]) -> None:
        self._tts[name] = cls

    def register_telephony(self, name: str, cls: type[TelephonyProvider]) -> None:
        self._telephony[name] = cls

    def get_stt(self, name: str) -> type[STTProvider]:
        if name not in self._stt:
            raise KeyError(f"STT provider '{name}' not registered. Available: {list(self._stt)}")
        return self._stt[name]

    def get_llm(self, name: str) -> type[LLMProvider]:
        if name not in self._llm:
            raise KeyError(f"LLM provider '{name}' not registered. Available: {list(self._llm)}")
        return self._llm[name]

    def get_tts(self, name: str) -> type[TTSProvider]:
        if name not in self._tts:
            raise KeyError(f"TTS provider '{name}' not registered. Available: {list(self._tts)}")
        return self._tts[name]

    def get_telephony(self, name: str) -> type[TelephonyProvider]:
        if name not in self._telephony:
            raise KeyError(
                f"Telephony provider '{name}' not registered. Available: {list(self._telephony)}"
            )
        return self._telephony[name]


# Global singleton
registry = ProviderRegistry()