"""
# Cartesia Sonic 3 TTS — ultra-low-latency streaming
# THE PRIMARY TTS — ~90ms time-to-first-byte
# Key features:
#   WebSocket streaming with continuations:
#     context_id maintains prosody across chunked text inputs
#     continue_=True keeps the same "sentence feel" across multiple sends
#   Text streaming input: accepts tokens as they arrive from LLM
#   Sentence-boundary flushing: buffers until ".", "!", "?" for natural prosody
# How synthesize_stream() works:
#   1. Receives async token stream from LLM
#   2. Buffers until sentence boundary or 80 chars
#   3. Sends text chunk to Cartesia via WebSocket
#   4. Yields audio chunks as they arrive
#   5. On barge-in: cancel() sends cancel message to stop synthesis
# Output: mulaw 8kHz (direct telephony format, no transcoding needed)
# Registered as: registry.register_tts("cartesia", ...)
"""

"""
Cartesia Sonic TTS provider — ultra-low-latency streaming synthesis.

Key features:
- WebSocket streaming with continuations (context_id maintains prosody)
- Text streaming input (accepts tokens as they arrive from LLM)
- ~90ms time-to-first-byte
- 42 languages including Hindi and 10 Indian regional languages
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncIterator

from cartesia import AsyncCartesia

from app.voice.providers.base import TTSAudioChunk, TTSProvider, registry

logger = logging.getLogger(__name__)


class CartesiaTTSProvider(TTSProvider):
    provider_name = "cartesia"

    def __init__(self) -> None:
        self._client: AsyncCartesia | None = None
        self._ws: Any = None
        self._voice_id: str = ""
        self._model: str = "sonic-3"
        self._language: str = "en"
        self._sample_rate: int = 8000  # 8kHz for telephony (Twilio μ-law)
        self._output_encoding: str = "pcm_mulaw"  # Direct telephony format
        self._context_id: str | None = None
        self._is_connected = False

    async def connect(self, config: dict[str, Any]) -> None:
        """
        Initialize Cartesia client and open WebSocket.

        Config keys:
            api_key: str (required)
            voice_id: str (required)
            model: str (default: sonic-3)
            language: str (default: en)
            sample_rate: int (default: 8000 for telephony)
            output_encoding: str (default: pcm_mulaw for Twilio)
        """
        api_key = config.get("api_key", "")
        if not api_key:
            raise ValueError("Cartesia API key is required")

        self._voice_id = config.get("voice_id", "")
        if not self._voice_id:
            raise ValueError("Cartesia voice_id is required")

        self._model = config.get("model", "sonic-3")
        self._language = config.get("language", "en")
        self._sample_rate = config.get("sample_rate", 8000)
        self._output_encoding = config.get("output_encoding", "pcm_mulaw")

        self._client = AsyncCartesia(api_key=api_key)
        self._ws = self._client.tts.websocket()
        await self._ws.connect()
        self._is_connected = True

        logger.info(
            "Cartesia TTS connected: model=%s voice=%s rate=%d encoding=%s",
            self._model, self._voice_id, self._sample_rate, self._output_encoding,
        )

    async def synthesize_stream(
        self, text_stream: AsyncIterator[str]
    ) -> AsyncIterator[TTSAudioChunk]:
        """
        Accept streaming text tokens from LLM, yield audio chunks as synthesized.

        Uses Cartesia's continuation feature (context_id) to maintain prosody
        across multiple text chunks within a single turn. This is the key
        to achieving pipeline parallelism: LLM tokens → TTS audio → telephony
        all flowing concurrently.
        """
        if not self._ws or not self._is_connected:
            raise ConnectionError("Cartesia TTS not connected")

        # New context for each turn
        self._context_id = str(uuid.uuid4())
        text_buffer = ""
        chunk_count = 0

        try:
            async for token in text_stream:
                text_buffer += token

                # Send text in sentence-sized chunks for better prosody.
                # Buffer until we hit a sentence boundary or accumulate enough text.
                if self._should_flush(text_buffer):
                    async for audio_chunk in self._send_text_chunk(
                        text_buffer, is_final=False
                    ):
                        chunk_count += 1
                        yield audio_chunk
                    text_buffer = ""

            # Flush any remaining text
            if text_buffer.strip():
                async for audio_chunk in self._send_text_chunk(
                    text_buffer, is_final=True
                ):
                    chunk_count += 1
                    yield audio_chunk

            logger.debug(
                "Cartesia TTS turn complete: %d audio chunks produced", chunk_count
            )

        except Exception as e:
            logger.error("Cartesia streaming synthesis error: %s", e)
            raise

    def _should_flush(self, buffer: str) -> bool:
        """Determine if the text buffer should be sent to TTS."""
        # Flush on sentence boundaries for natural prosody
        if any(buffer.rstrip().endswith(p) for p in (".", "!", "?", ":", ";")):
            return True
        # Flush on comma with enough context
        if buffer.rstrip().endswith(",") and len(buffer) > 30:
            return True
        # Flush if buffer is getting long (avoid latency on run-on sentences)
        if len(buffer) > 80:
            return True
        return False

    async def _send_text_chunk(
        self, text: str, is_final: bool = False
    ) -> AsyncIterator[TTSAudioChunk]:
        """Send a text chunk to Cartesia and yield audio output."""
        try:
            output = await self._ws.send(
                model_id=self._model,
                transcript=text,
                voice_id=self._voice_id,
                output_format={
                    "container": "raw",
                    "encoding": self._output_encoding,
                    "sample_rate": self._sample_rate,
                },
                language=self._language,
                context_id=self._context_id,
                continue_=not is_final,  # Continue prosody within the turn
            )

            async for chunk in output:
                if hasattr(chunk, "audio") and chunk.audio:
                    yield TTSAudioChunk(
                        data=chunk.audio,
                        sample_rate=self._sample_rate,
                        encoding=self._output_encoding,
                        is_final=False,
                    )

            if is_final:
                yield TTSAudioChunk(
                    data=b"",
                    sample_rate=self._sample_rate,
                    encoding=self._output_encoding,
                    is_final=True,
                )

        except Exception as e:
            logger.error("Cartesia chunk synthesis error: %s", e)
            raise

    async def synthesize(self, text: str) -> bytes:
        """Synthesize complete text to audio bytes (for cached/short responses)."""
        if not self._client:
            raise ConnectionError("Cartesia client not initialized")

        output = await self._client.tts.bytes(
            model_id=self._model,
            transcript=text,
            voice_id=self._voice_id,
            output_format={
                "container": "raw",
                "encoding": self._output_encoding,
                "sample_rate": self._sample_rate,
            },
            language=self._language,
        )
        return output

    async def cancel(self) -> None:
        """Cancel current synthesis (called on barge-in)."""
        if self._ws and self._context_id:
            try:
                await self._ws.send(
                    model_id=self._model,
                    transcript="",
                    voice_id=self._voice_id,
                    output_format={
                        "container": "raw",
                        "encoding": self._output_encoding,
                        "sample_rate": self._sample_rate,
                    },
                    context_id=self._context_id,
                    continue_=False,
                    cancel=True,
                )
            except Exception as e:
                logger.warning("Error canceling Cartesia synthesis: %s", e)
            self._context_id = None

    async def close(self) -> None:
        """Close WebSocket and client."""
        self._is_connected = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._client:
            await self._client.close()
            self._client = None


# Register with the provider registry
registry.register_tts("cartesia", CartesiaTTSProvider)