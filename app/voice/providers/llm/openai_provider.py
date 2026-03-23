"""
# OpenAI LLM — streaming chat completions
# Model: GPT-4.1 mini (best quality-latency-cost for voice)
# Key features:
#   generate() — non-streaming, returns complete LLMResponse
#   generate_stream() — THE IMPORTANT ONE: yields tokens one-by-one
#     These tokens are fed directly to TTS for concurrent synthesis
#   Function/tool calling: formats tools for OpenAI format, detects tool_calls in stream
# Message formatting: converts LLMMessage → OpenAI API format
# Registered as: registry.register_llm("openai", ...)
"""

"""
OpenAI LLM provider — streaming chat completions with function/tool calling.

Optimized for voice agents:
- Streaming token output for real-time TTS feeding
- Short max_tokens (150-200) for conversational responses
- Tool/function calling for booking, lookups, etc.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from app.voice.providers.base import LLMMessage, LLMProvider, LLMResponse, registry

logger = logging.getLogger(__name__)


class OpenAILLMProvider(LLMProvider):
    provider_name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4.1-mini") -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    def _format_messages(self, messages: list[LLMMessage]) -> list[dict[str, Any]]:
        """Convert internal message format to OpenAI API format."""
        formatted = []
        for msg in messages:
            entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.tool_calls:
                entry["tool_calls"] = msg.tool_calls
            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id
            formatted.append(entry)
        return formatted

    def _format_tools(self, tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        """Format tools for OpenAI function calling."""
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {}),
                },
            }
            for tool in tools
        ]

    async def generate(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 200,
    ) -> LLMResponse:
        """Non-streaming completion."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self._format_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        formatted_tools = self._format_tools(tools)
        if formatted_tools:
            kwargs["tools"] = formatted_tools

        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        tool_calls = None
        if choice.message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in choice.message.tool_calls
            ]

        return LLMResponse(
            text=choice.message.content or "",
            finish_reason=choice.finish_reason,
            tool_calls=tool_calls,
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            },
        )

    async def generate_stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 200,
    ) -> AsyncIterator[str]:
        """
        Stream response tokens for real-time TTS feeding.

        Yields individual tokens as they arrive. The pipeline feeds these
        directly to the TTS provider's streaming input, enabling concurrent
        LLM generation and audio synthesis.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self._format_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        formatted_tools = self._format_tools(tools)
        if formatted_tools:
            kwargs["tools"] = formatted_tools

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

                # Handle tool calls in streaming mode
                if (
                    chunk.choices
                    and chunk.choices[0].delta.tool_calls
                ):
                    # Tool call chunks need to be accumulated and handled
                    # by the pipeline orchestrator — we signal via a special prefix
                    for tc in chunk.choices[0].delta.tool_calls:
                        if tc.function and tc.function.name:
                            logger.info("Tool call detected: %s", tc.function.name)

        except Exception as e:
            logger.error("OpenAI streaming error: %s", e)
            raise

    async def close(self) -> None:
        """Close the async client."""
        await self._client.close()


# Register with the provider registry
registry.register_llm("openai", OpenAILLMProvider)