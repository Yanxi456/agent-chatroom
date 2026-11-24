"""Run the chatroom server with ``python -m server.main``."""

import asyncio
import logging

from agent.types import RunTask
from shared import protocol as p

from server import config
from server.handlers import Handlers
from server.scheduler import AgentScheduler
from server.session import OnlineSessions, Session
from server.storage import Storage


class ChatServer:
    def __init__(
        self, storage: Storage, *, idle_timeout: float = p.IDLE_TIMEOUT_SECONDS,
        run_task: RunTask | None = None, agent_timeout: float = p.AGENT_TIMEOUT_SECONDS,
    ) -> None:
        self._storage = storage
        self._idle_timeout = idle_timeout
        self._online = OnlineSessions()
        self._scheduler = AgentScheduler(storage, self._online, run_task=run_task, timeout=agent_timeout)
        self._handlers = Handlers(storage, self._online, self._scheduler)
        self._listener: asyncio.Server | None = None
        self._sessions: dict[asyncio.Task, Session] = {}

    async def start(self, host: str = config.HOST, port: int = config.PORT) -> asyncio.Server:
        await self._storage.initialize()
        self._listener = await asyncio.start_server(self._accept, host, port)
        return self._listener

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session = Session(reader, writer, self._online, self._idle_timeout)
        task = asyncio.create_task(session.run(self._handlers.handle))
        self._sessions[task] = session
        task.add_done_callback(self._sessions.pop)

    async def close(self) -> None:
        if self._listener is not None:
            self._listener.close()
        sessions = list(self._sessions.items())
        for _, session in sessions:
            session.abort()
        if self._listener is not None:
            await self._listener.wait_closed()
        await asyncio.gather(*(task for task, _ in sessions))
        await self._scheduler.close()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    server = ChatServer(Storage(config.DB_PATH))
    listener = await server.start()
    try:
        logging.info("Chatroom listening on %s", [sock.getsockname() for sock in listener.sockets])
        # start() already accepts connections. Wait for cancellation so close()
        # can disconnect clients before waiting for the listener to finish.
        await asyncio.Event().wait()
    finally:
        await server.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
