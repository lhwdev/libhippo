"""Core data models and event definitions for the General Coding Agent Harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine, Literal


class ExecutionMode(str, Enum):
    """Operational autonomy modes for the harness."""

    TURBO = "turbo"
    DEFAULT = "default"
    REQUEST_REVIEW = "request_review"


@dataclass
class ContextMessage:
    """Represents a message within the 3-Zone context memory architecture."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    zone: Literal["zone1_prefix", "zone2_linear", "zone3_compacted"]
    tool_call_id: str | None = None
    file_path_reference: str | None = None
    raw_token_count: int = 0
    is_evictable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolDefinition:
    """Schema and handler binding for a harness tool."""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    handler: Callable[..., Coroutine[Any, Any, Any]]
    requires_sandbox_bypass: bool = False
    requires_approval: bool = False


# --- Asynchronous Streaming Events ---

@dataclass
class TokenChunkEvent:
    delta: str
    type: str = "token_chunk"


@dataclass
class ToolCallStartEvent:
    tool_call_id: str
    name: str
    arguments: dict[str, Any]
    type: str = "tool_call_start"


@dataclass
class ToolCallResultEvent:
    tool_call_id: str
    name: str
    result: Any
    error: str | None = None
    type: str = "tool_call_result"


@dataclass
class ApprovalRequestEvent:
    request_id: str
    action: str
    details: dict[str, Any]
    type: str = "approval_request"


@dataclass
class ModalQuestionEvent:
    question_id: str
    questions: list[dict[str, Any]]
    type: str = "modal_question"


@dataclass
class TaskNotificationEvent:
    task_id: str
    status: str
    log_path: str
    exit_code: int | None = None
    type: str = "task_notification"


@dataclass
class PhaseTransitionEvent:
    from_phase: str
    to_phase: str
    type: str = "phase_transition"


@dataclass
class TurnCompletedEvent:
    turn_index: int
    total_tokens: int
    duration_seconds: float
    response: str = ""
    type: str = "turn_completed"


@dataclass
class InterruptEvent:
    reason: str
    interrupted_at: str
    type: str = "interrupt"


HarnessEvent = (
    TokenChunkEvent
    | ToolCallStartEvent
    | ToolCallResultEvent
    | ApprovalRequestEvent
    | ModalQuestionEvent
    | TaskNotificationEvent
    | PhaseTransitionEvent
    | TurnCompletedEvent
    | InterruptEvent
)
