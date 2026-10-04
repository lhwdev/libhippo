"""Targeted Responses WebSocket transport adapter with resilient HTTP fallback using openai[realtime]."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator, Sequence

from autogen_core import FunctionCall
from autogen_core.models import (
    ChatCompletionClient,
    CreateResult,
    LLMMessage,
    ModelCapabilities,
    ModelInfo,
    RequestUsage,
    SystemMessage,
    UserMessage,
    AssistantMessage,
    FunctionExecutionResultMessage,
)
from openai import AsyncOpenAI
import uuid


class OpenAIResponsesClient(ChatCompletionClient):
    """Adapter for modern OpenAI Responses API (/v1/responses) supporting both reasoning and tools."""

    def __init__(
        self,
        model: str = "gpt-6.1-sol",
        api_key: str | None = None,
        base_url: str | None = None,
        reasoning_effort: str | None = "medium",
        model_info: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url
        self.reasoning_effort = reasoning_effort
        self.kwargs = kwargs
        self.last_response_id: str | None = None
        self._client = AsyncOpenAI(api_key=self.api_key or "mock-key", base_url=self.base_url)
        self._total_usage = RequestUsage(prompt_tokens=0, completion_tokens=0)
        self._actual_usage = RequestUsage(prompt_tokens=0, completion_tokens=0)

        effective_info = model_info or {
            "vision": True,
            "function_calling": True,
            "json_output": True,
            "family": "unknown",
            "structured_output": True,
            "multiple_system_messages": True,
        }
        self._model_info = ModelInfo(**effective_info)

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
        return self._actual_usage

    def total_usage(self) -> RequestUsage:
        return self._total_usage

    def count_tokens(self, messages: Sequence[LLMMessage], tools: Sequence[Any] = []) -> int:
        return 0

    def remaining_tokens(self, messages: Sequence[LLMMessage], tools: Sequence[Any] = []) -> int:
        return 128000

    async def close(self) -> None:
        await self._client.close()

    def _convert_messages(self, messages: Sequence[LLMMessage]) -> tuple[str | None, list[dict[str, Any]]]:
        instructions: list[str] = []
        input_items: list[dict[str, Any]] = []
        for m in messages:
            if isinstance(m, SystemMessage):
                instructions.append(m.content)
            elif isinstance(m, UserMessage):
                input_items.append({"role": "user", "content": m.content, "type": "message"})
            elif isinstance(m, AssistantMessage):
                if isinstance(m.content, list):
                    for fc in m.content:
                        input_items.append({
                            "type": "function_call",
                            "call_id": getattr(fc, "id", None) or f"call_{uuid.uuid4().hex[:8]}",
                            "name": getattr(fc, "name", ""),
                            "arguments": fc.arguments if isinstance(fc.arguments, str) else json.dumps(fc.arguments),
                        })
                else:
                    input_items.append({"role": "assistant", "content": m.content, "type": "message"})
            elif isinstance(m, FunctionExecutionResultMessage):
                for fer in m.content:
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": fer.call_id,
                        "output": str(fer.content),
                    })
        instr = "\n\n".join(instructions) if instructions else None
        return instr, input_items

    def _convert_tools(self, tools: Sequence[Any]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for t in tools:
            if isinstance(t, dict):
                if "type" in t and "function" in t:
                    fn = t["function"]
                    formatted.append({
                        "type": "function",
                        "name": fn.get("name"),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                    })
                elif t.get("type") == "function":
                    formatted.append(t)
                else:
                    formatted.append({
                        "type": "function",
                        "name": t.get("name"),
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {}),
                    })
            elif hasattr(t, "name"):
                params = getattr(t, "parameters", None)
                if params is None and hasattr(t, "schema"):
                    params = getattr(t.schema, "parameters", {})
                formatted.append({
                    "type": "function",
                    "name": t.name,
                    "description": getattr(t, "description", "") or "",
                    "parameters": params or {},
                })
        return formatted

    def _parse_response(self, response: Any) -> CreateResult:
        self.last_response_id = getattr(response, "id", None)
        tool_calls: list[FunctionCall] = []
        text_parts: list[str] = []
        thought_parts: list[str] = []

        for item in getattr(response, "output", []):
            item_type = getattr(item, "type", "")
            if item_type == "function_call":
                tool_calls.append(
                    FunctionCall(
                        id=getattr(item, "call_id", None) or getattr(item, "id", f"call_{uuid.uuid4().hex[:8]}"),
                        name=getattr(item, "name", ""),
                        arguments=getattr(item, "arguments", "{}"),
                    )
                )
            elif item_type == "message":
                content = getattr(item, "content", None)
                if isinstance(content, list):
                    for c in content:
                        text_parts.append(getattr(c, "text", str(c)))
                elif content:
                    text_parts.append(str(content))
            elif item_type == "reasoning":
                for s in getattr(item, "summary", []) or []:
                    text = getattr(s, "text", str(s))
                    if text:
                        thought_parts.append(text)
                for c in getattr(item, "content", []) or []:
                    text = getattr(c, "text", str(c))
                    if text:
                        thought_parts.append(text)

        if not tool_calls and not text_parts and getattr(response, "output_text", None):
            text_parts.append(response.output_text)

        final_content = tool_calls if tool_calls else "".join(text_parts)
        thought = "\n".join(thought_parts) if thought_parts else None
        finish_reason = "function_calls" if tool_calls else "stop"

        p_tokens = getattr(response.usage, "input_tokens", 0) if hasattr(response, "usage") and response.usage else 0
        c_tokens = getattr(response.usage, "output_tokens", 0) if hasattr(response, "usage") and response.usage else 0
        usage = RequestUsage(prompt_tokens=p_tokens, completion_tokens=c_tokens)

        self._actual_usage = usage
        self._total_usage = RequestUsage(
            prompt_tokens=self._total_usage.prompt_tokens + p_tokens,
            completion_tokens=self._total_usage.completion_tokens + c_tokens,
        )

        return CreateResult(
            finish_reason=finish_reason,
            content=final_content,
            usage=usage,
            cached=getattr(response, "prompt_cache_diagnostics", None) is not None,
            thought=thought,
        )

    def _get_request_items(
        self, messages: Sequence[LLMMessage]
    ) -> tuple[str | None, list[dict[str, Any]], str | None]:
        """Extract instructions, input items, and optional previous_response_id for delta chaining."""
        if not self.last_response_id or not messages:
            instructions, input_items = self._convert_messages(messages)
            return instructions, input_items, None

        # Find the last AssistantMessage to identify the delta messages since the last turn
        last_asst_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], AssistantMessage):
                last_asst_idx = i
                break

        if last_asst_idx is not None and last_asst_idx < len(messages) - 1:
            delta_messages = messages[last_asst_idx + 1 :]
            _, delta_items = self._convert_messages(delta_messages)
            if delta_items:
                return None, delta_items, self.last_response_id

        instructions, input_items = self._convert_messages(messages)
        return instructions, input_items, None

    async def create(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[Any] = [],
        json_output: bool | None = None,
        extra_create_args: dict[str, Any] = {},
        cancellation_token: Any | None = None,
    ) -> CreateResult:
        instructions, input_items, prev_id = self._get_request_items(messages)
        formatted_tools = self._convert_tools(tools)

        req_kwargs: dict[str, Any] = {
            "model": self.model,
            "input": input_items,
        }
        if instructions:
            req_kwargs["instructions"] = instructions
        if formatted_tools:
            req_kwargs["tools"] = formatted_tools

        effort = extra_create_args.get("reasoning_effort", self.reasoning_effort)
        if effort and effort != "none":
            req_kwargs["reasoning"] = {"effort": effort}

        temp = extra_create_args.get("temperature", self.kwargs.get("temperature"))
        if temp is not None:
            req_kwargs["temperature"] = temp

        if prev_id:
            req_kwargs["previous_response_id"] = prev_id

        for k, v in self.kwargs.items():
            if k not in req_kwargs and k not in ("temperature", "reasoning_effort"):
                req_kwargs[k] = v

        try:
            response = await self._client.responses.create(**req_kwargs)
            return self._parse_response(response)
        except Exception:
            if prev_id:
                self.last_response_id = None
                instructions_full, input_items_full = self._convert_messages(messages)
                req_kwargs["input"] = input_items_full
                if instructions_full:
                    req_kwargs["instructions"] = instructions_full
                else:
                    req_kwargs.pop("instructions", None)
                req_kwargs.pop("previous_response_id", None)
                response = await self._client.responses.create(**req_kwargs)
                return self._parse_response(response)
            raise

    async def create_stream(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[Any] = [],
        json_output: bool | None = None,
        extra_create_args: dict[str, Any] = {},
        cancellation_token: Any | None = None,
    ) -> AsyncIterator[Any]:
        instructions, input_items, prev_id = self._get_request_items(messages)
        formatted_tools = self._convert_tools(tools)

        req_kwargs: dict[str, Any] = {
            "model": self.model,
            "input": input_items,
            "stream": True,
        }
        if instructions:
            req_kwargs["instructions"] = instructions
        if formatted_tools:
            req_kwargs["tools"] = formatted_tools

        effort = extra_create_args.get("reasoning_effort", self.reasoning_effort)
        if effort and effort != "none":
            req_kwargs["reasoning"] = {"effort": effort}

        temp = extra_create_args.get("temperature", self.kwargs.get("temperature"))
        if temp is not None:
            req_kwargs["temperature"] = temp

        if prev_id:
            req_kwargs["previous_response_id"] = prev_id

        for k, v in self.kwargs.items():
            if k not in req_kwargs and k not in ("temperature", "reasoning_effort"):
                req_kwargs[k] = v

        try:
            stream_resp = await self._client.responses.create(**req_kwargs)
        except Exception:
            if prev_id:
                self.last_response_id = None
                instructions_full, input_items_full = self._convert_messages(messages)
                req_kwargs["input"] = input_items_full
                if instructions_full:
                    req_kwargs["instructions"] = instructions_full
                else:
                    req_kwargs.pop("instructions", None)
                req_kwargs.pop("previous_response_id", None)
                stream_resp = await self._client.responses.create(**req_kwargs)
            else:
                raise

        async for event in stream_resp:
            ev_type = getattr(event, "type", "")
            if ev_type == "response.text.delta":
                yield getattr(event, "delta", "")
            elif ev_type in ("response.completed", "response.incomplete"):
                resp_obj = getattr(event, "response", None)
                if resp_obj:
                    yield self._parse_response(resp_obj)


class OpenAIResponsesWebSocketClient(ChatCompletionClient):
    """Adapter connecting to OpenAI Responses WebSocket API with automatic HTTP fallback to /v1/responses."""

    def __init__(
        self,
        model: str = "gpt-6.1-sol",
        api_key: str | None = None,
        base_url: str | None = None,
        ws_url: str = "wss://api.openai.com/v1/responses",
        reasoning_effort: str | None = "medium",
        model_info: dict[str, Any] | None = None,
        enable_http_fallback: bool = True,
        **kwargs: Any,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url
        self.ws_url = ws_url
        self.reasoning_effort = reasoning_effort
        self.enable_http_fallback = enable_http_fallback
        self.kwargs = kwargs
        self._last_response_id: str | None = None
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

        filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}

        # Fallback HTTP client powered by OpenAIResponsesClient targeting /v1/responses
        self.http_client = OpenAIResponsesClient(
            model=model,
            api_key=self.api_key or "mock-key",
            base_url=base_url,
            reasoning_effort=reasoning_effort,
            model_info=self._model_info,
            **filtered_kwargs,
        )

        self._client: AsyncOpenAI | None = None

    @property
    def last_response_id(self) -> str | None:
        return self._last_response_id

    @last_response_id.setter
    def last_response_id(self, val: str | None) -> None:
        self._last_response_id = val
        if hasattr(self, "http_client"):
            self.http_client.last_response_id = val

    def _get_request_items(
        self, messages: Sequence[LLMMessage]
    ) -> tuple[str | None, list[dict[str, Any]], str | None]:
        return self.http_client._get_request_items(messages)

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
        """Establish persistent WebSocket connection via openai.responses.connect()."""
        if self._is_connected and self._connection is not None:
            return self._connection

        try:
            effective_key = self.api_key or os.environ.get("OPENAI_API_KEY", "mock-key")
            self._client = AsyncOpenAI(api_key=effective_key, base_url=self.base_url)
            mgr = self._client.responses.connect()
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
                return await self._create_via_websocket(
                    messages=messages,
                    tools=tools,
                    extra_create_args=extra_create_args,
                )
            except Exception:
                if not self.enable_http_fallback:
                    raise
                self._is_connected = False

        # Fallback to HTTP client using /v1/responses
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
        extra_create_args: dict[str, Any] = {},
    ) -> CreateResult:
        """Execute delta turn over live WebSocket connection with delta chaining."""
        instructions, input_items, prev_id = self._get_request_items(messages)
        formatted_tools = self.http_client._convert_tools(tools)

        create_event: dict[str, Any] = {
            "type": "response.create",
            "model": self.model,
            "input": input_items,
        }
        if instructions:
            create_event["instructions"] = instructions
        if formatted_tools:
            create_event["tools"] = formatted_tools
        if prev_id:
            create_event["previous_response_id"] = prev_id

        effort = extra_create_args.get("reasoning_effort", self.reasoning_effort)
        if effort and effort != "none":
            create_event["reasoning"] = {"effort": effort}

        temp = extra_create_args.get("temperature", self.kwargs.get("temperature"))
        if temp is not None:
            create_event["temperature"] = temp

        for k, v in self.kwargs.items():
            if k not in create_event and k not in ("temperature", "reasoning_effort", "model"):
                create_event[k] = v

        await self._connection.send(create_event)

        resp_obj = None
        async for event in self._connection:
            event_type = getattr(event, "type", "")
            if event_type == "response.created":
                resp = getattr(event, "response", None)
                if resp:
                    self.last_response_id = getattr(resp, "id", None)
            elif event_type in ("response.completed", "response.incomplete"):
                resp_obj = getattr(event, "response", None)
                if resp_obj:
                    self.last_response_id = getattr(resp_obj, "id", self.last_response_id)
                break
            elif event_type in ("response.failed", "response.error"):
                err = getattr(event, "error", None) or getattr(event, "response", None)
                raise RuntimeError(f"WebSocket response failed: {err}")

        if resp_obj is None:
            raise RuntimeError("WebSocket connection closed before response completed")

        return self.http_client._parse_response(resp_obj)

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
                instructions, input_items, prev_id = self._get_request_items(messages)
                formatted_tools = self.http_client._convert_tools(tools)

                create_event: dict[str, Any] = {
                    "type": "response.create",
                    "model": self.model,
                    "input": input_items,
                    "stream": True,
                }
                if instructions:
                    create_event["instructions"] = instructions
                if formatted_tools:
                    create_event["tools"] = formatted_tools
                if prev_id:
                    create_event["previous_response_id"] = prev_id

                effort = extra_create_args.get("reasoning_effort", self.reasoning_effort)
                if effort and effort != "none":
                    create_event["reasoning"] = {"effort": effort}

                temp = extra_create_args.get("temperature", self.kwargs.get("temperature"))
                if temp is not None:
                    create_event["temperature"] = temp

                for k, v in self.kwargs.items():
                    if k not in create_event and k not in ("temperature", "reasoning_effort", "model", "stream"):
                        create_event[k] = v

                await self._connection.send(create_event)

                completed = False
                async for event in self._connection:
                    event_type = getattr(event, "type", "")
                    if event_type == "response.created":
                        resp = getattr(event, "response", None)
                        if resp:
                            self.last_response_id = getattr(resp, "id", None)
                    elif event_type == "response.text.delta":
                        yield getattr(event, "delta", "")
                    elif event_type in ("response.completed", "response.incomplete"):
                        resp_obj = getattr(event, "response", None)
                        if resp_obj:
                            self.last_response_id = getattr(resp_obj, "id", self.last_response_id)
                            yield self.http_client._parse_response(resp_obj)
                        completed = True
                        break
                    elif event_type in ("response.failed", "response.error"):
                        err = getattr(event, "error", None) or getattr(event, "response", None)
                        raise RuntimeError(f"WebSocket response failed: {err}")

                if not completed:
                    raise RuntimeError("WebSocket closed before receiving response completion")
                return
            except Exception:
                if not self.enable_http_fallback:
                    raise
                self._is_connected = False

        # Fallback to HTTP streaming using /v1/responses
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
