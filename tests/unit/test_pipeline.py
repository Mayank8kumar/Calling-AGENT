"""
# Pipeline component tests (10 tests)
# TestProviderRegistry:
#   - deepgram STT registered and retrievable
#   - openai LLM registered
#   - cartesia TTS registered
#   - twilio telephony registered
#   - fallback providers (anthropic, elevenlabs, plivo) registered
#   - unknown provider raises KeyError
# TestPipelineConfig:
#   - default config values correct
#   - custom config override works
# TestTurnMetrics:
#   - latency calculations (stt, llm_ttft, tts_ttfb)
#   - to_dict() serialization
# TestDataClasses:
#   - AudioChunk, TranscriptSegment, LLMMessage creation
"""

"""Tests for voice pipeline — provider registry, config, metrics."""

import os
import pytest
import asyncio

os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-validation")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-long")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from app.voice.providers.base import (
    AudioChunk,
    LLMMessage,
    LLMProvider,
    LLMResponse,
    ProviderRegistry,
    STTProvider,
    TTSAudioChunk,
    TTSProvider,
    TranscriptSegment,
    registry,
)
from app.voice.pipeline import PipelineConfig, TurnMetrics


class TestProviderRegistry:
    def test_register_and_retrieve_stt(self):
        """Providers registered at import time should be accessible."""
        import app.voice.providers.stt.deepgram_provider  # noqa: F401
        cls = registry.get_stt("deepgram")
        assert cls.provider_name == "deepgram"

    def test_register_and_retrieve_llm(self):
        import app.voice.providers.llm.openai_provider  # noqa: F401
        cls = registry.get_llm("openai")
        assert cls.provider_name == "openai"

    def test_register_and_retrieve_tts(self):
        import app.voice.providers.tts.cartesia_provider  # noqa: F401
        cls = registry.get_tts("cartesia")
        assert cls.provider_name == "cartesia"

    def test_register_and_retrieve_telephony(self):
        import app.voice.providers.telephony.twilio_provider  # noqa: F401
        cls = registry.get_telephony("twilio")
        assert cls.provider_name == "twilio"

    def test_unknown_provider_raises(self):
        with pytest.raises(KeyError, match="not registered"):
            registry.get_stt("nonexistent_provider")

    def test_fallback_providers_registered(self):
        import app.voice.providers.llm.anthropic_provider  # noqa: F401
        import app.voice.providers.tts.elevenlabs_provider  # noqa: F401
        import app.voice.providers.telephony.plivo_provider  # noqa: F401

        assert registry.get_llm("anthropic").provider_name == "anthropic"
        assert registry.get_tts("elevenlabs").provider_name == "elevenlabs"
        assert registry.get_telephony("plivo").provider_name == "plivo"


class TestPipelineConfig:
    def test_default_config(self):
        config = PipelineConfig()
        assert config.stt_provider == "deepgram"
        assert config.llm_provider == "openai"
        assert config.tts_provider == "cartesia"
        assert config.llm_max_tokens == 200
        assert config.enable_barge_in is True

    def test_custom_config(self):
        config = PipelineConfig(
            stt_provider="deepgram",
            llm_provider="anthropic",
            tts_provider="elevenlabs",
            llm_temperature=0.5,
        )
        assert config.llm_provider == "anthropic"
        assert config.tts_provider == "elevenlabs"
        assert config.llm_temperature == 0.5


class TestTurnMetrics:
    def test_latency_calculations(self):
        m = TurnMetrics(
            turn_id="t1",
            stt_start_ms=100,
            stt_end_ms=250,
            llm_start_ms=250,
            llm_first_token_ms=500,
            llm_end_ms=800,
            tts_start_ms=300,
            tts_first_audio_ms=400,
            tts_end_ms=900,
            total_ms=800,
        )
        assert m.stt_latency_ms == 150
        assert m.llm_ttft_ms == 250
        assert m.tts_ttfb_ms == 100

    def test_to_dict(self):
        m = TurnMetrics(turn_id="t1", total_ms=500)
        d = m.to_dict()
        assert d["turn_id"] == "t1"
        assert d["total_ms"] == 500
        assert "stt_ms" in d


class TestDataClasses:
    def test_audio_chunk(self):
        chunk = AudioChunk(data=b"\x00" * 160, sample_rate=8000)
        assert len(chunk.data) == 160
        assert chunk.encoding == "mulaw"

    def test_transcript_segment(self):
        seg = TranscriptSegment(text="hello", is_final=True, confidence=0.95)
        assert seg.text == "hello"
        assert seg.is_final

    def test_llm_message(self):
        msg = LLMMessage(role="user", content="hi there")
        assert msg.role == "user"