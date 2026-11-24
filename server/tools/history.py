"""History tools bound to an Agent task's requester and conversation."""

from agent.errors import ToolError
from agent.types import AgentContext, AgentTools, HistoryPage
from server.storage import Storage, StorageError
from server.validation import RequestError, history_limit, message_id, trimmed_text
from shared import protocol as p


def build_history_tools(storage: Storage, context: AgentContext) -> AgentTools:
    requester_id = context["requester"]["user_id"]
    conversation_id = context["conversation_id"]
    trigger_message_id = context["trigger_message_id"]

    async def query(
        before_id: int | None, limit: int, *, keyword: str | None = None
    ) -> HistoryPage:
        try:
            before_id = message_id(before_id) if before_id is not None else None
            limit = history_limit(limit)
            if keyword is not None:
                keyword = trimmed_text(keyword, p.MAX_KEYWORD_LENGTH)
        except RequestError as exc:
            raise ToolError(p.INVALID_ARGUMENT, str(exc)) from None

        try:
            return await storage.history(
                requester_id,
                conversation_id,
                before_id=before_id,
                limit=limit,
                keyword=keyword,
                max_id_exclusive=trigger_message_id,
            )
        except Exception as exc:
            if isinstance(exc, StorageError) and exc.code in {p.FORBIDDEN, p.CONVERSATION_NOT_FOUND}:
                raise ToolError(p.FORBIDDEN, "History is unavailable to this requester") from None
            raise ToolError(p.INTERNAL_ERROR, "History query failed") from None

    async def read_history(
        *, before_id: int | None = None, limit: int = p.DEFAULT_HISTORY_LIMIT, **extra: object
    ) -> HistoryPage:
        if extra:
            raise ToolError(p.INVALID_ARGUMENT, "Unexpected history tool arguments")
        return await query(before_id, limit)

    async def search_history(
        *, keyword: str | None = None, before_id: int | None = None,
        limit: int = p.DEFAULT_HISTORY_LIMIT, **extra: object
    ) -> HistoryPage:
        if keyword is None or extra:
            raise ToolError(p.INVALID_ARGUMENT, "Missing keyword or unexpected history tool arguments")
        return await query(before_id, limit, keyword=keyword)

    return {"read_history": read_history, "search_history": search_history}
