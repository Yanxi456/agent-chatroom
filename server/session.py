"""TCP connection lifetime, serialized output, and online presence."""

import asyncio
from collections.abc import Awaitable, Callable
import logging

from agent.types import Message, User
from shared.codec import FrameDecoder, FrameError, encode_frame
from shared import protocol as p

from server.validation import UnusableRequest


logger = logging.getLogger(__name__)
_MAX_PENDING_FRAMES = 256


class OnlineSessions:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def add(self, session: "Session", user: User) -> bool:
        user_id = user["user_id"]
        if user_id in self._sessions:
            return False
        self._sessions[user_id] = session
        session.user = user
        return True

    def remove(self, session: "Session") -> None:
        if session.user is None:
            return
        user_id = session.user["user_id"]
        session.user = None
        if self._sessions.get(user_id) is session:
            del self._sessions[user_id]
            self.broadcast_presence()

    def users(self) -> list[User]:
        return sorted(
            (session.user for session in self._sessions.values() if session.user is not None),
            key=lambda user: user["username"],
        )

    def broadcast_presence(self) -> None:
        message = {"v": p.PROTOCOL_VERSION, "type": p.PRESENCE_CHANGED, "data": {"users": self.users()}}
        try:
            frame = encode_frame(message)
        except FrameError:
            logger.warning("Online presence snapshot exceeds the frame limit")
            return
        for session in self._sessions.values():
            session.enqueue(frame)

    def broadcast_message(
        self, message: Message, members: tuple[str, ...] | None, *, exclude: str | None = None,
    ) -> None:
        frame = encode_frame({
            "v": p.PROTOCOL_VERSION, "type": p.MESSAGE_NEW, "data": {"message": message},
        })
        recipients = self._sessions if members is None else members
        for user_id in recipients:
            session = self._sessions.get(user_id)
            if user_id != exclude and session is not None:
                session.enqueue(frame)

    def send_event(self, user_id: str, event_type: str, data: dict) -> None:
        session = self._sessions.get(user_id)
        if session is not None:
            session.send({"v": p.PROTOCOL_VERSION, "type": event_type, "data": data})


class Session:
    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        online: OnlineSessions, idle_timeout: float,
    ) -> None:
        self.user: User | None = None
        self.request_ids: set[str] = set()
        self._reader = reader
        self._writer = writer
        self._online = online
        self._idle_timeout = idle_timeout
        self._outgoing: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_MAX_PENDING_FRAMES)
        self._closing = False

    def send(self, message: dict) -> None:
        self.enqueue(encode_frame(message))

    def enqueue(self, frame: bytes) -> None:
        if self._closing:
            return
        try:
            self._outgoing.put_nowait(frame)
        except asyncio.QueueFull:
            self.abort()

    def abort(self) -> None:
        self._closing = True
        self._writer.transport.abort()

    async def _write_frames(self) -> None:
        try:
            while (frame := await self._outgoing.get()) is not None:
                self._writer.write(frame)
                await self._writer.drain()
        except OSError:
            self.abort()

    async def _read_frames(self, handle: Callable[["Session", dict], Awaitable[bool]]) -> bool:
        decoder = FrameDecoder()
        clock = asyncio.get_running_loop().time
        deadline = clock() + self._idle_timeout
        while not self._closing:
            async with asyncio.timeout_at(deadline):
                chunk = await self._reader.read(65536)
            if not chunk:
                break
            messages = decoder.feed(chunk)
            if messages:
                deadline = clock() + self._idle_timeout
            for message in messages:
                if self._closing:
                    return False
                if not await handle(self, message):
                    return True  # Logout acknowledges the request before closing.
                # Give the writer a turn even with a batch of in-memory requests.
                await asyncio.sleep(0)
        return False

    async def run(self, handle: Callable[["Session", dict], Awaitable[bool]]) -> None:
        writing = asyncio.create_task(self._write_frames())
        try:
            if await self._read_frames(handle):
                self._online.remove(self)
                async with asyncio.timeout(self._idle_timeout):
                    await self._outgoing.put(None)
                    await writing
                    self._writer.close()
                    await asyncio.shield(self._writer.wait_closed())
        except (FrameError, UnusableRequest, OSError, TimeoutError):
            pass
        finally:
            self._online.remove(self)
            self.abort()
            writing.cancel()
            await asyncio.gather(writing, return_exceptions=True)
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
