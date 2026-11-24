"""SQLite-backed users, conversations, and message history."""

import asyncio
from collections.abc import Iterator
from contextlib import closing, contextmanager
from hashlib import scrypt
from hmac import compare_digest
from pathlib import Path
from secrets import token_bytes
import sqlite3
import time
from typing import Literal, TypedDict
from uuid import uuid4

from agent.types import HistoryPage, Message, User
from shared import protocol as p


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    salt BLOB,
    password_hash BLOB
);
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('group', 'dm'))
);
CREATE TABLE IF NOT EXISTS conversation_members (
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    user_id TEXT NOT NULL REFERENCES users(user_id),
    PRIMARY KEY (conversation_id, user_id)
);
CREATE INDEX IF NOT EXISTS members_by_user ON conversation_members(user_id);
CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT CHECK (message_id BETWEEN 1 AND {p.MAX_MESSAGE_ID}),
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    sender_id TEXT NOT NULL REFERENCES users(user_id),
    kind TEXT NOT NULL CHECK (kind IN ('user', 'agent')),
    text TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    task_id TEXT
);
CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages(conversation_id, message_id);
"""


class Conversation(TypedDict):
    conversation_id: str
    kind: Literal["group", "dm"]
    peer: User | None


class StorageError(Exception):
    """A database operation rejected by a chatroom business rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _password_hash(password: str, salt: bytes) -> bytes:
    return scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)


def _user(row: sqlite3.Row) -> User:
    return {"user_id": row["user_id"], "username": row["username"]}


def _message(row: sqlite3.Row) -> Message:
    return {
        "message_id": row["message_id"],
        "conversation_id": row["conversation_id"],
        "sender": _user(row),
        "kind": row["kind"],
        "text": row["text"],
        "created_at_ms": row["created_at_ms"],
        "task_id": row["task_id"],
    }


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.path = path
        # A password hash uses about 16 MiB of working memory.
        self._hash_slots = asyncio.Semaphore(4)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL").fetchone()
            db.executescript(_SCHEMA)
            db.execute(
                "INSERT INTO users (user_id, username) VALUES (?, ?) ON CONFLICT(user_id) DO NOTHING",
                (p.AGENT_USER_ID, p.AGENT_USERNAME),
            )
            db.execute(
                "INSERT INTO conversations VALUES (?, 'group') ON CONFLICT(conversation_id) DO NOTHING",
                (p.GENERAL_CONVERSATION_ID,),
            )

    async def _hash(self, password: str, salt: bytes) -> bytes:
        async with self._hash_slots:
            return await asyncio.to_thread(_password_hash, password, salt)

    async def register(self, username: str, password: str) -> User:
        username = username.lower()
        if username == p.RESERVED_USERNAME:
            raise StorageError(p.INVALID_ARGUMENT, "Username is reserved")
        salt = token_bytes(16)
        password_hash = await self._hash(password, salt)
        return await asyncio.to_thread(self._register, username, salt, password_hash)

    def _register(self, username: str, salt: bytes, password_hash: bytes) -> User:
        user_id = f"usr_{uuid4().hex}"
        with self._connect() as db:
            result = db.execute(
                """INSERT INTO users VALUES (?, ?, ?, ?)
                   ON CONFLICT(username) DO NOTHING""",
                (user_id, username, salt, password_hash),
            )
            if result.rowcount == 0:
                raise StorageError(p.USERNAME_TAKEN, "Username is already registered")
        return {"user_id": user_id, "username": username}

    async def authenticate(self, username: str, password: str) -> User:
        row = await asyncio.to_thread(self._credentials, username.lower())
        if row is not None:
            password_hash = await self._hash(password, row["salt"])
            if compare_digest(password_hash, row["password_hash"]):
                return _user(row)
        raise StorageError(p.INVALID_CREDENTIALS, "Invalid username or password")

    def _credentials(self, username: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM users WHERE username = ? AND password_hash IS NOT NULL", (username,)
            ).fetchone()

    async def open_dm(self, user_id: str, peer_username: str) -> Conversation:
        return await asyncio.to_thread(self._open_dm, user_id, peer_username.lower())

    def _open_dm(self, user_id: str, peer_username: str) -> Conversation:
        with self._connect() as db:
            peer = db.execute(
                """SELECT user_id, username FROM users
                   WHERE username = ? AND password_hash IS NOT NULL""",
                (peer_username,),
            ).fetchone()
            if peer is None:
                raise StorageError(p.USER_NOT_FOUND, "User was not found")
            peer_id = peer["user_id"]
            if peer_id == user_id:
                raise StorageError(p.INVALID_ARGUMENT, "Cannot open a direct conversation with yourself")

            first, second = sorted((user_id, peer_id))
            conversation_id = f"dm:{first}:{second}"
            db.execute(
                "INSERT INTO conversations VALUES (?, 'dm') ON CONFLICT(conversation_id) DO NOTHING",
                (conversation_id,),
            )
            db.executemany(
                """INSERT INTO conversation_members VALUES (?, ?)
                   ON CONFLICT(conversation_id, user_id) DO NOTHING""",
                [(conversation_id, first), (conversation_id, second)],
            )
        return {"conversation_id": conversation_id, "kind": "dm", "peer": _user(peer)}

    async def list_conversations(self, user_id: str) -> list[Conversation]:
        return await asyncio.to_thread(self._list_conversations, user_id)

    def _list_conversations(self, user_id: str) -> list[Conversation]:
        with self._connect() as db:
            peers = db.execute(
                """SELECT mine.conversation_id, users.user_id, users.username
                   FROM conversation_members AS mine
                   JOIN conversation_members AS other ON other.conversation_id = mine.conversation_id
                   JOIN users ON users.user_id = other.user_id
                   WHERE mine.user_id = ? AND other.user_id != mine.user_id
                   ORDER BY users.username""",
                (user_id,),
            ).fetchall()
        conversations: list[Conversation] = [
            {"conversation_id": p.GENERAL_CONVERSATION_ID, "kind": "group", "peer": None}
        ]
        conversations.extend(
            {"conversation_id": row["conversation_id"], "kind": "dm", "peer": _user(row)}
            for row in peers
        )
        return conversations

    async def check_access(self, user_id: str, conversation_id: str) -> None:
        await asyncio.to_thread(self._get_members, user_id, conversation_id)

    async def conversation_members(self, user_id: str, conversation_id: str) -> tuple[str, ...] | None:
        """Check access and return DM recipients, or None for the public room."""
        return await asyncio.to_thread(self._get_members, user_id, conversation_id)

    def _get_members(self, user_id: str, conversation_id: str) -> tuple[str, ...] | None:
        with self._connect() as db:
            return self._members(db, user_id, conversation_id)

    @staticmethod
    def _members(
        db: sqlite3.Connection, user_id: str, conversation_id: str
    ) -> tuple[str, ...] | None:
        conversation = db.execute(
            "SELECT kind FROM conversations WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            raise StorageError(p.CONVERSATION_NOT_FOUND, "Conversation was not found")
        if conversation["kind"] == "group":
            return None
        members = tuple(row["user_id"] for row in db.execute(
            "SELECT user_id FROM conversation_members WHERE conversation_id = ?", (conversation_id,)
        ))
        if user_id not in members:
            raise StorageError(p.FORBIDDEN, "You do not have access to this conversation")
        return members

    async def write_message(
        self,
        user_id: str,
        conversation_id: str,
        text: str,
        *,
        kind: Literal["user", "agent"] = "user",
        task_id: str | None = None,
    ) -> Message:
        return await asyncio.to_thread(
            self._write_message, user_id, conversation_id, text, kind, task_id
        )

    def _write_message(
        self, user_id: str, conversation_id: str, text: str, kind: Literal["user", "agent"],
        task_id: str | None,
    ) -> Message:
        with self._connect() as db:
            self._members(db, user_id, conversation_id)
            sender_id = p.AGENT_USER_ID if kind == "agent" else user_id
            sender = db.execute("SELECT user_id, username FROM users WHERE user_id = ?", (sender_id,)).fetchone()
            created_at_ms = time.time_ns() // 1_000_000
            inserted = db.execute(
                """INSERT INTO messages (conversation_id, sender_id, kind, text, created_at_ms, task_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (conversation_id, sender_id, kind, text, created_at_ms, task_id),
            )
        return {
            "message_id": inserted.lastrowid,
            "conversation_id": conversation_id,
            "sender": _user(sender),
            "kind": kind,
            "text": text,
            "created_at_ms": created_at_ms,
            "task_id": task_id,
        }

    async def history(
        self,
        user_id: str,
        conversation_id: str,
        *,
        before_id: int | None = None,
        limit: int = p.DEFAULT_HISTORY_LIMIT,
        keyword: str | None = None,
        max_id_exclusive: int | None = None,
    ) -> HistoryPage:
        bounds = [bound for bound in (before_id, max_id_exclusive) if bound is not None]
        upper_bound = min(bounds, default=p.MAX_MESSAGE_ID + 1)
        return await asyncio.to_thread(
            self._history, user_id, conversation_id, upper_bound, limit, keyword
        )

    def _history(
        self, user_id: str, conversation_id: str, upper_bound: int, limit: int, keyword: str | None
    ) -> HistoryPage:
        with self._connect() as db:
            self._members(db, user_id, conversation_id)
            query = """SELECT messages.*, users.user_id, users.username
                       FROM messages JOIN users ON messages.sender_id = users.user_id
                       WHERE conversation_id = ? AND message_id < ?"""
            parameters: list[str | int] = [conversation_id, upper_bound]
            if keyword is not None:
                # SQLite's built-in lower() folds ASCII letters only.
                query += " AND instr(lower(text), lower(?)) > 0"
                parameters.append(keyword)
            query += " ORDER BY message_id DESC LIMIT ?"
            parameters.append(limit + 1)
            rows = db.execute(query, parameters).fetchall()

        messages = [_message(row) for row in reversed(rows[:limit])]
        next_before_id = messages[0]["message_id"] if len(rows) > limit else None
        return {"messages": messages, "next_before_id": next_before_id}
