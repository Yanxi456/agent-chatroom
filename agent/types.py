"""Shared types for the backend's Agent runner and history tools."""

from collections.abc import Awaitable, Callable
from typing import Literal, Protocol, TypedDict


class User(TypedDict):
    user_id: str
    username: str


class Message(TypedDict):
    message_id: int
    conversation_id: str
    sender: User
    kind: Literal["user", "agent"]
    text: str
    created_at_ms: int
    task_id: str | None


class AgentContext(TypedDict):
    task_id: str
    requester: User
    conversation_id: str
    trigger_message_id: int
    mode: Literal["auto", "summary", "todos"]
    instruction: str


class HistoryPage(TypedDict):
    messages: list[Message]
    next_before_id: int | None


class ReadHistory(Protocol):
    def __call__(
        self, *, before_id: int | None = None, limit: int = 20
    ) -> Awaitable[HistoryPage]: ...


class SearchHistory(Protocol):
    def __call__(
        self, *, keyword: str, before_id: int | None = None, limit: int = 20
    ) -> Awaitable[HistoryPage]: ...


class AgentTools(TypedDict):
    read_history: ReadHistory
    search_history: SearchHistory


class ToolCall(TypedDict):
    call_id: str
    name: Literal["read_history", "search_history"]


class ToolErrorInfo(TypedDict):
    code: Literal["INVALID_ARGUMENT", "FORBIDDEN", "INTERNAL_ERROR"]
    message: str


class ToolStarted(ToolCall):
    state: Literal["started"]


class ToolSucceeded(ToolCall):
    state: Literal["succeeded"]
    result_count: int


class ToolFailed(ToolCall):
    state: Literal["failed"]
    error: ToolErrorInfo


class AgentResult(TypedDict):
    text: str


class RunTask(Protocol):
    async def __call__(
        self,
        context: AgentContext,
        tools: AgentTools,
        emit: Callable[[ToolStarted | ToolSucceeded | ToolFailed], Awaitable[None]],
    ) -> AgentResult: ...
