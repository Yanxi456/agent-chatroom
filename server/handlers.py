"""Validate TCP requests and dispatch chatroom operations."""

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
import logging

from agent.types import AgentContext, Message, User
from shared import protocol as p

from server.scheduler import AgentScheduler
from server.session import OnlineSessions, Session
from server.storage import Storage, StorageError
from server.validation import parse_request, request_id, RequestError


logger = logging.getLogger(__name__)


@dataclass
class Reply:
    result: dict = field(default_factory=dict)
    after_send: Callable[[], None] | None = None
    close: bool = False


class Handlers:
    def __init__(self, storage: Storage, online: OnlineSessions, scheduler: AgentScheduler) -> None:
        self._storage = storage
        self._online = online
        self._scheduler = scheduler

    async def handle(self, session: Session, request: dict) -> bool:
        correlation_id = request_id(request)
        response = {"v": p.PROTOCOL_VERSION, "type": p.RESPONSE, "request_id": correlation_id}
        try:
            if correlation_id in session.request_ids:
                raise RequestError(p.INVALID_ARGUMENT, "Request ID has already been used")
            session.request_ids.add(correlation_id)
            request_type, data = parse_request(request)
            reply = await self._dispatch(session, request_type, data)
            session.send({**response, "data": {"ok": True, "result": reply.result}})
        except (RequestError, StorageError) as exc:
            error = {"code": exc.code, "message": str(exc)}
        except Exception as exc:
            logger.warning("Request processing failed (%s)", type(exc).__name__)
            error = {"code": p.INTERNAL_ERROR, "message": "Request processing failed"}
        else:
            if reply.after_send is not None:
                reply.after_send()
            return not reply.close

        session.send({**response, "data": {"ok": False, "error": error}})
        return True

    async def _dispatch(self, session: Session, request_type: str, data: dict) -> Reply:
        if request_type == p.PING:
            return Reply()
        if request_type in (p.AUTH_REGISTER, p.AUTH_LOGIN):
            if session.user is not None:
                raise RequestError(p.INVALID_STATE, "This connection is already logged in")
            if request_type == p.AUTH_REGISTER:
                user = await self._storage.register(data["username"], data["password"])
                return Reply({"user": user})
            user = await self._storage.authenticate(data["username"], data["password"])
            if not self._online.add(session, user):
                raise RequestError(p.ALREADY_ONLINE, "This user is already online")
            return Reply({"user": user}, after_send=self._online.broadcast_presence)

        if session.user is None:
            raise RequestError(p.AUTH_REQUIRED, "Log in first")
        if request_type == p.AUTH_LOGOUT:
            return Reply(close=True)
        user_id = session.user["user_id"]
        if request_type == p.USERS_ONLINE:
            return Reply({"users": self._online.users()})
        if request_type == p.CONVERSATION_LIST:
            conversations = await self._storage.list_conversations(user_id)
            return Reply({"conversations": conversations})
        if request_type == p.CONVERSATION_OPEN_DM:
            conversation = await self._storage.open_dm(user_id, data["peer_username"])
            return Reply({"conversation": conversation})
        if request_type == p.MESSAGE_SEND:
            conversation_id = data["conversation_id"]
            members = await self._storage.conversation_members(user_id, conversation_id)
            message = await self._storage.write_message(user_id, conversation_id, data["text"])
            broadcast = partial(self._online.broadcast_message, message, members, exclude=user_id)
            return Reply({"message": message}, after_send=broadcast)
        if request_type == p.HISTORY_LIST:
            return Reply(await self._storage.history(user_id, **data))
        if request_type == p.AGENT_RUN:
            return await self._agent_run(session.user, data)
        raise RuntimeError("Validated request has no handler")

    async def _agent_run(self, requester: User, data: dict) -> Reply:
        requester = requester.copy()
        user_id = requester["user_id"]
        conversation_id = data["conversation_id"]
        instruction = data["instruction"]
        members = await self._storage.conversation_members(user_id, conversation_id)
        task_id = self._scheduler.reserve(user_id)
        try:
            trigger = await self._storage.write_message(
                user_id, conversation_id, p.AGENT_PREFIX + instruction, task_id=task_id,
            )
        except BaseException:
            self._scheduler.release(user_id)
            raise

        context: AgentContext = {
            "task_id": task_id,
            "requester": requester,
            "conversation_id": conversation_id,
            "trigger_message_id": trigger["message_id"],
            "mode": data.get("mode", p.DEFAULT_AGENT_MODE),
            "instruction": instruction,
        }
        publish = partial(self._publish_agent_trigger, context, trigger, members)
        return Reply({"task_id": task_id, "message": trigger}, after_send=publish)

    def _publish_agent_trigger(
        self, context: AgentContext, trigger: Message, members: tuple[str, ...] | None,
    ) -> None:
        self._online.broadcast_message(trigger, members, exclude=context["requester"]["user_id"])
        self._scheduler.submit(context, members)
