"""
# Anthropic Claude — fallback LLM
# Model: Claude Haiku 4.5 (fast, affordable fallback)
# Same interface as OpenAI provider:
#   generate() — non-streaming
#   generate_stream() — streaming via messages.stream()
# Differences from OpenAI:
#   - System prompt is separate from messages (Anthropic API requirement)
#   - Tool use format slightly different (input_schema vs parameters)
# Used when: OpenAI circuit breaker is open, or tenant configures Claude
# Registered as: registry.register_llm("anthropic", ...)
"""

"""
Anthropic Claude LLM provider — fallback LLM option.

Supports streaming responses and tool use via the Anthropic Messages API.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from app.voice.providers.base import LLMMessage, LLMProvider, LLMResponse, registry

logger = logging.getLogger(__name__)


class AnthropicLLMProvider(LLMProvider):
    provider_name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001") -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    def _format_messages(
        self, messages: list[LLMMessage]
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Convert to Anthropic format.
        Anthropic separates system prompt from messages.
        Returns (system_prompt, messages).
        """
        system = ""
        formatted = []

        for msg in messages:
            if msg.role == "system":
                system = msg.content
            else:
                role = "user" if msg.role == "user" else "assistant"
                formatted.append({"role": role, "content": msg.content})

        return system, formatted

    def _format_tools(self, tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": tool.get("parameters", {"type": "object", "properties": {}}),
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
        system, msgs = self._format_messages(messages)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": msgs,
        }
        if system:
            kwargs["system"] = system

        formatted_tools = self._format_tools(tools)
        if formatted_tools:
            kwargs["tools"] = formatted_tools

        response = await self._client.messages.create(**kwargs)

        text = ""
        tool_calls = None
        for block in response.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append({
                    "id": block.id,
                    "function": {"name": block.name, "arguments": str(block.input)},
                })

        return LLMResponse(
            text=text,
            finish_reason=response.stop_reason,
            tool_calls=tool_calls,
            usage={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
            },
        )

    async def generate_stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 200,
    ) -> AsyncIterator[str]:
        system, msgs = self._format_messages(messages)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": msgs,
        }
        if system:
            kwargs["system"] = system

        formatted_tools = self._format_tools(tools)
        if formatted_tools:
            kwargs["tools"] = formatted_tools

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:
            logger.error("Anthropic streaming error: %s", e)
            raise

    async def close(self) -> None:
        await self._client.close()


registry.register_llm("anthropic", AnthropicLLMProvider)