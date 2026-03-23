"""
# ElevenLabs Flash v2.5 — fallback TTS
# Higher voice quality than Cartesia, but 2-3x more expensive
# Uses REST streaming (not WebSocket) for simpler implementation
# How synthesize_stream() works:
#   1. Accumulates text until sentence boundary
#   2. POSTs chunk to /v1/text-to-speech/{voice_id} with stream=True
#   3. Reads response body as streaming audio chunks
# Output format: ulaw_8000 (direct telephony compatible)
# Used when: Cartesia circuit breaker open, or tenant prefers ElevenLabs
# Registered as: registry.register_tts("elevenlabs", ...)
"""

"""
ElevenLabs TTS provider — high-quality voice synthesis fallback.

Used as secondary TTS when Cartesia is unavailable or for tenants
that prefer ElevenLabs voice quality.

Supports WebSocket streaming via the input-streaming API.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

import httpx

from app.voice.providers.base import TTSAudioChunk, TTSProvider, registry

logger = logging.getLogger(__name__)

ELEVENLABS_WS_URL = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"
ELEVENLABS_REST_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


class ElevenLabsTTSProvider(TTSProvider):
    provider_name = "elevenlabs"

    def __init__(self) -> None:
        self._api_key: str = ""
        self._voice_id: str = ""
        self._model: str = "eleven_flash_v2_5"
        self._sample_rate: int = 8000
        self._output_format: str = "ulaw_8000"
        self._is_connected = False

    async def connect(self, config: dict[str, Any]) -> None:
        """
        Config keys:
            api_key: str (required)
            voice_id: str (required)
            model: str (default: eleven_flash_v2_5)
            sample_rate: int (default: 8000)
            output_format: str (default: ulaw_8000)
        """
        self._api_key = config.get("api_key", "")
        if not self._api_key:
            raise ValueError("ElevenLabs API key is required")

        self._voice_id = config.get("voice_id", "")
        if not self._voice_id:
            raise ValueError("ElevenLabs voice_id is required")

        self._model = config.get("model", "eleven_flash_v2_5")
        self._sample_rate = config.get("sample_rate", 8000)
        self._output_format = config.get("output_format", "ulaw_8000")
        self._is_connected = True

        logger.info(
            "ElevenLabs TTS ready: model=%s voice=%s format=%s",
            self._model, self._voice_id, self._output_format,
        )

    async def synthesize_stream(
        self, text_stream: AsyncIterator[str]
    ) -> AsyncIterator[TTSAudioChunk]:
        """
        Stream text tokens from LLM → ElevenLabs input-streaming API → audio chunks.

        Uses the REST streaming endpoint with chunked text accumulation.
        For true WebSocket streaming, use the WS API (more complex setup).
        """
        if not self._is_connected:
            raise ConnectionError("ElevenLabs TTS not connected")

        # Accumulate text in sentence-sized chunks for better prosody
        text_buffer = ""
        async for token in text_stream:
            text_buffer += token

            # Flush on sentence boundaries
            if any(text_buffer.rstrip().endswith(p) for p in (".", "!", "?", ":")):
                if text_buffer.strip():
                    async for chunk in self._synthesize_chunk(text_buffer.strip()):
                        yield chunk
                    text_buffer = ""

        # Flush remaining
        if text_buffer.strip():
            async for chunk in self._synthesize_chunk(text_buffer.strip()):
                yield chunk

        yield TTSAudioChunk(data=b"", sample_rate=self._sample_rate, is_final=True)

    async def _synthesize_chunk(self, text: str) -> AsyncIterator[TTSAudioChunk]:
        """Synthesize a text chunk via REST streaming endpoint."""
        url = ELEVENLABS_REST_URL.format(voice_id=self._voice_id)

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                url,
                headers={
                    "xi-api-key": self._api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": self._model,
                    "output_format": self._output_format,
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                    },
                },
                params={"optimize_streaming_latency": 3},
                timeout=30.0,
            ) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    logger.error("ElevenLabs API error %d: %s", response.status_code, error_text[:200])
                    return

                async for audio_bytes in response.aiter_bytes(chunk_size=4096):
                    if audio_bytes:
                        yield TTSAudioChunk(
                            data=audio_bytes,
                            sample_rate=self._sample_rate,
                            encoding="mulaw" if "ulaw" in self._output_format else "pcm_s16le",
                            is_final=False,
                        )

    async def synthesize(self, text: str) -> bytes:
        """Synthesize complete text to audio bytes."""
        url = ELEVENLABS_REST_URL.format(voice_id=self._voice_id)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers={
                    "xi-api-key": self._api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": self._model,
                    "output_format": self._output_format,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            return response.content

    async def close(self) -> None:
        self._is_connected = False


# Register
registry.register_tts("elevenlabs", ElevenLabsTTSProvider)