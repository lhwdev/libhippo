"""Targeted Responses WebSocket transport adapter with resilient HTTP fallback using openai[realtime]."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator, Sequence

from autogen_core.models import (
    ChatCompletionClient,
    CreateResult,
    LLMMessage,
    ModelCapabilities,
    ModelInfo,
    RequestUsage,
)
from autogen_ext.models.openai import OpenAIChatCompletionClient
from openai import AsyncOpenAI


class OpenAIResponsesWebSocketClient(ChatCompletionClient):
    """Adapter connecting to OpenAI Responses/Realtime WebSocket API with automatic HTTP fallback."""

    def __init__(
        self,
        model: str = "gpt-6.1-sol",
        api_key: str | None = None,
        base_url: str | None = None,
        ws_url: str = "wss://api.openai.com/v1/responses",
        model_info: dict[str, Any] | None = None,
        enable_http_fallback: bool = True,
        **kwargs: Any,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url
        self.ws_url = ws_url
        self.enable_http_fallback = enable_http_fallback
        self.kwargs = kwargs
        self.last_response_id: str | None = None
        self._is_connected: bool = False
        self._connection: Any = None
        self._pending_steer: str | None = None

        effective_info = model_info or {
            "vision": True,
            "function_calling": True,
            "json_output": True,
            "family": "unknown",
            "structured_output": True,
            "multiple_system_messages": True,
        }
        self._model_info = ModelInfo(**effective_info)

        # Filter out None values
        filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}

        # Fallback HTTP client powered by AutoGen built-in OpenAIChatCompletionClient
        self.http_client = OpenAIChatCompletionClient(
            model=model,
            api_key=self.api_key or "mock-key",
            base_url=base_url,
            model_info=self._model_info,
            **filtered_kwargs,
        )

        # Realtime client from openai[realtime]
        self._realtime_client: AsyncOpenAI | None = None

    @property
    def model_info(self) -> ModelInfo:
        return self._model_info

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            vision=self._model_info.get("vision", True),
            function_calling=self._model_info.get("function_calling", True),
            json_output=self._model_info.get("json_output", True),
        )

    def actual_usage(self) -> RequestUsage:
        return self.http_client.actual_usage()

    def total_usage(self) -> RequestUsage:
        return self.http_client.total_usage()

    async def connect(self) -> Any:
        """Establish persistent WebSocket connection via openai[realtime]."""
        if self._is_connected and self._connection is not None:
            return self._connection

        try:
            effective_key = self.api_key or os.environ.get("OPENAI_API_KEY", "mock-key")
            self._realtime_client = AsyncOpenAI(api_key=effective_key, base_url=self.base_url)
            mgr = self._realtime_client.realtime.connect(model=self.model)
            self._connection = await mgr.enter()
            self._is_connected = True
            return self._connection
        except Exception:
            self._is_connected = False
            self._connection = None
            if not self.enable_http_fallback:
                raise
            return None

    async def create(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[Any] = [],
        json_output: bool | None = None,
        extra_create_args: dict[str, Any] = {},
        cancellation_token: Any | None = None,
    ) -> CreateResult:
        """Create completion using persistent WebSocket delta chaining, falling back to HTTP."""
        if not self._is_connected:
            await self.connect()

        if self._is_connected and self._connection is not None:
            try:
                return await self._create_via_websocket(messages, tools)
            except Exception:
                if not self.enable_http_fallback:
                    raise
                self._is_connected = False

        # Fallback to HTTP client
        return await self.http_client.create(
            messages=messages,
            tools=tools,
            json_output=json_output,
            extra_create_args=extra_create_args,
            cancellation_token=cancellation_token,
        )

    async def _create_via_websocket(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[Any] = [],
    ) -> CreateResult:
        """Execute delta turn over live WebSocket connection with delta chaining."""
        last_user_msg = messages[-1].content if messages else ""
        delta_payload = {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": str(last_user_msg)}],
            },
        }

        await self._connection.send(delta_payload)

        # Trigger response creation with previous_response_id
        create_event: dict[str, Any] = {"type": "response.create"}
        if self.last_response_id:
            create_event["previous_response_id"] = self.last_response_id
        await self._connection.send(create_event)

        collected_text: list[str] = []
        resp_id = None

        async for event in self._connection:
            event_type = getattr(event, "type", "")
            if event_type == "response.created":
                resp = getattr(event, "response", None)
                if resp:
                    resp_id = getattr(resp, "id", None)
                    self.last_response_id = resp_id
            elif event_type == "response.text.delta":
                delta = getattr(event, "delta", "")
                collected_text.append(delta)
            elif event_type in ("response.text.done", "response.done"):
                break
            elif event_type == "response.incomplete":
                break

        full_content = "".join(collected_text)
        return CreateResult(
            finish_reason="stop",
            content=full_content,
            usage=RequestUsage(prompt_tokens=len(str(last_user_msg)), completion_tokens=len(full_content)),
            cached=self.last_response_id is not None,
        )

    async def create_stream(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[Any] = [],
        json_output: bool | None = None,
        extra_create_args: dict[str, Any] = {},
        cancellation_token: Any | None = None,
    ) -> AsyncIterator[Any]:
        """Stream completion chunks over live WebSocket with HTTP fallback."""
        if not self._is_connected:
            await self.connect()

        if self._is_connected and self._connection is not None:
            try:
                last_user_msg = messages[-1].content if messages else ""
                await self._connection.send(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": str(last_user_msg)}],
                        },
                    }
                )
                create_event: dict[str, Any] = {"type": "response.create"}
                if self.last_response_id:
                    create_event["previous_response_id"] = self.last_response_id
                await self._connection.send(create_event)

                async for event in self._connection:
                    event_type = getattr(event, "type", "")
                    if event_type == "response.created":
                        resp = getattr(event, "response", None)
                        if resp:
                            self.last_response_id = getattr(resp, "id", None)
                    elif event_type == "response.text.delta":
                        yield getattr(event, "delta", "")
                    elif event_type in ("response.text.done", "response.done", "response.incomplete"):
                        break
                return
            except Exception:
                if not self.enable_http_fallback:
                    raise
                self._is_connected = False

        # Fallback to HTTP streaming
        async for chunk in self.http_client.create_stream(
            messages=messages,
            tools=tools,
            json_output=json_output,
            extra_create_args=extra_create_args,
            cancellation_token=cancellation_token,
        ):
            yield chunk

    async def steer(self, input_text: str) -> dict[str, Any]:
        """Send mid-turn steering message over WebSocket without tearing down connection."""
        self._pending_steer = input_text
        if self._is_connected and self._connection is not None:
            try:
                steer_event = {
                    "type": "response.steer",
                    "previous_response_id": self.last_response_id,
                    "input": input_text,
                }
                await self._connection.send(steer_event)
                return {"status": "steered_over_websocket", "event": steer_event}
            except Exception:
                pass
        return {"status": "steer_queued", "input": input_text}

    async def cancel(self) -> dict[str, Any]:
        """Cancel active in-flight response over WebSocket."""
        if self._is_connected and self._connection is not None:
            try:
                await self._connection.send({"type": "response.cancel"})
                return {"status": "cancelled_over_websocket"}
            except Exception:
                pass
        return {"status": "cancelled_local"}

    async def count_tokens(self, messages: Sequence[LLMMessage], tools: Sequence[Any] = []) -> int:
        return await self.http_client.count_tokens(messages=messages, tools=tools)

    def remaining_tokens(self, messages: Sequence[LLMMessage], tools: Sequence[Any] = []) -> int:
        return self.http_client.remaining_tokens(messages=messages, tools=tools)

    async def close(self) -> None:
        """Close WebSocket and HTTP resources."""
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception:
                pass
        self._connection = None
        self._is_connected = False
        await self.http_client.close()
