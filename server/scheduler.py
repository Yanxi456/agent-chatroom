"""Reserve, queue, and execute Agent tasks independently of TCP readers."""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from uuid import uuid4

from agent.errors import AgentExecutionError, AgentUnavailableError, ToolError
from agent.types import (
    AgentContext, AgentResult, AgentTools, RunTask, ToolFailed, ToolStarted, ToolSucceeded,
)
from shared import protocol as p

from server.session import OnlineSessions
from server.storage import Storage
from server.tools.history import build_history_tools
from server.validation import RequestError, trimmed_text


logger = logging.getLogger(__name__)
ToolEvent = ToolStarted | ToolSucceeded | ToolFailed


async def _default_runner(
    context: AgentContext, tools: AgentTools, emit: Callable[[ToolEvent], Awaitable[None]],
) -> AgentResult:
    try:
        from agent.runner import run_task
    except ModuleNotFoundError as exc:
        if exc.name != "agent.runner":
            raise
        raise AgentUnavailableError("Agent runner is not installed") from exc
    return await run_task(context, tools, emit)


class _ExecutionTimedOut(Exception):
    pass


@dataclass
class _Job:
    context: AgentContext
    members: tuple[str, ...] | None


class AgentScheduler:
    def __init__(
        self, storage: Storage, online: OnlineSessions, *, run_task: RunTask | None = None,
        timeout: float = p.AGENT_TIMEOUT_SECONDS,
    ) -> None:
        self._storage = storage
        self._online = online
        self._run_task = _default_runner if run_task is None else run_task
        self._timeout = timeout
        self._reserved: set[str] = set()
        self._queued: deque[_Job] = deque()
        self._running: dict[asyncio.Task[None], str] = {}
        self._closed = False

    def reserve(self, user_id: str) -> str:
        capacity = p.MAX_RUNNING_AGENT_TASKS + p.MAX_QUEUED_AGENT_TASKS
        if user_id in self._reserved or len(self._reserved) >= capacity:
            raise RequestError(p.AGENT_BUSY, "Agent task capacity is full")
        self._reserved.add(user_id)
        return uuid4().hex

    def release(self, user_id: str) -> None:
        self._reserved.remove(user_id)

    def submit(self, context: AgentContext, members: tuple[str, ...] | None) -> None:
        self._queued.append(_Job(context, members))
        self._start_ready()

    def _start_ready(self) -> None:
        while not self._closed and self._queued and len(self._running) < p.MAX_RUNNING_AGENT_TASKS:
            job = self._queued.popleft()
            task = asyncio.create_task(self._execute(job))
            self._running[task] = job.context["requester"]["user_id"]
            task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self.release(self._running.pop(task))
        self._start_ready()

    async def close(self) -> None:
        self._closed = True
        self._queued.clear()
        running = list(self._running)
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        self._reserved.clear()

    async def _execute(self, job: _Job) -> None:
        context = job.context
        requester_id = context["requester"]["user_id"]
        conversation_id = context["conversation_id"]
        task_id = context["task_id"]
        scope = {"task_id": task_id, "conversation_id": conversation_id}
        deadline = asyncio.get_running_loop().time() + self._timeout
        self._online.send_event(requester_id, p.AGENT_TASK, {**scope, "state": "running"})

        try:
            result = await self._invoke(context, requester_id, scope, deadline)
            text = self._result_text(result)
            message = await self._storage.write_message(
                requester_id, conversation_id, text, kind="agent", task_id=task_id,
            )
            self._online.broadcast_message(message, job.members)
        except _ExecutionTimedOut:
            error = {"code": p.AGENT_TIMEOUT, "message": "Agent task timed out"}
        except AgentUnavailableError:
            error = {"code": p.AGENT_UNAVAILABLE, "message": "Agent model is unavailable"}
        except (AgentExecutionError, ToolError):
            error = {"code": p.AGENT_FAILED, "message": "Agent could not complete the task"}
        except Exception as exc:
            logger.warning("Agent task %s failed (%s)", task_id, type(exc).__name__)
            error = {"code": p.INTERNAL_ERROR, "message": "Agent task processing failed"}
        else:
            self._online.send_event(requester_id, p.AGENT_TASK, {
                **scope, "state": "succeeded", "message_id": message["message_id"],
            })
            return

        logger.info("Agent task %s ended with %s", task_id, error["code"])
        self._online.send_event(requester_id, p.AGENT_TASK, {**scope, "state": "failed", "error": error})

    @staticmethod
    def _result_text(result: AgentResult) -> str:
        if not isinstance(result, dict) or result.keys() != {"text"}:
            raise AgentExecutionError("Invalid Agent result")
        try:
            return trimmed_text(result["text"], p.MAX_TEXT_LENGTH)
        except RequestError as exc:
            raise AgentExecutionError("Invalid Agent result text") from exc

    async def _invoke(
        self, context: AgentContext, requester_id: str, scope: dict[str, str], deadline: float,
    ) -> AgentResult:
        tools = build_history_tools(self._storage, context)
        clock = asyncio.get_running_loop().time
        accepting_events = True
        finished_at = deadline

        async def emit(event: ToolEvent) -> None:
            if not accepting_events or clock() >= deadline:
                return
            state = event["state"]
            data: dict[str, object] = {
                **scope, "call_id": event["call_id"], "name": event["name"], "state": state,
            }
            if state == "succeeded":
                data["result_count"] = event["result_count"]
            elif state == "failed":
                error = event["error"]
                data["error"] = {"code": error["code"], "message": error["message"]}
            logger.info(
                "Agent tool task=%s call=%s name=%s state=%s count=%s error=%s",
                scope["task_id"], event["call_id"], event["name"], state,
                data.get("result_count"), event["error"]["code"] if state == "failed" else None,
            )
            # The session writer owns network errors. Enqueueing never waits on a socket.
            self._online.send_event(requester_id, p.AGENT_TOOL, data)

        async def run() -> AgentResult:
            nonlocal accepting_events, finished_at
            try:
                return await self._run_task(context, tools, emit)
            finally:
                finished_at = clock()
                accepting_events = False

        runner = asyncio.create_task(run())
        runner.add_done_callback(_consume_runner_exception)
        try:
            done, _ = await asyncio.wait({runner}, timeout=max(0, deadline - clock()))
            if not done or finished_at >= deadline:
                raise _ExecutionTimedOut
            if runner.cancelled():
                raise RuntimeError("Agent runner cancelled itself")
            return runner.result()
        finally:
            accepting_events = False
            if not runner.done():
                runner.cancel()


def _consume_runner_exception(task: asyncio.Task[AgentResult]) -> None:
    # A timed-out runner can finish after its scheduler slot has been released.
    if not task.cancelled():
        task.exception()
