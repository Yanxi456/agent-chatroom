import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from server.storage import Storage, StorageError
from shared import protocol as p


class StorageEdgesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "chatroom.sqlite3"
        self.storage = Storage(self.database)
        await self.storage.initialize()

    async def test_concurrent_case_variants_register_exactly_one_account(self):
        attempts = [("Alice", "first-password"), ("ALICE", "second-password")]
        results = await asyncio.gather(
            *(self.storage.register(name, password) for name, password in attempts),
            return_exceptions=True,
        )
        winners = [i for i, result in enumerate(results) if isinstance(result, dict)]
        self.assertEqual(len(winners), 1, results)
        winner = winners[0]
        loser = 1 - winner
        self.assertIsInstance(results[loser], StorageError)
        self.assertEqual(results[loser].code, p.USERNAME_TAKEN)
        authenticated = await self.storage.authenticate("aLiCe", attempts[winner][1])
        self.assertEqual(authenticated, results[winner])
        with self.assertRaises(StorageError) as failure:
            await self.storage.authenticate("aLiCe", attempts[loser][1])
        self.assertEqual(failure.exception.code, p.INVALID_CREDENTIALS)

    async def test_concurrent_writes_and_new_arrivals_during_pagination(self):
        alice = await self.storage.register("Alice", "test-password")
        bob = await self.storage.register("Bob", "test-password")
        dm = await self.storage.open_dm(alice["user_id"], "Bob")
        room = p.GENERAL_CONVERSATION_ID
        dm_id = dm["conversation_id"]

        async def send(user, conversation_id):
            return [
                await self.storage.write_message(user["user_id"], conversation_id, f"entry {i}")
                for i in range(6)
            ]

        batches = await asyncio.gather(
            send(alice, room), send(bob, room), send(alice, dm_id), send(bob, dm_id)
        )
        ids = [message["message_id"] for batch in batches for message in batch]
        self.assertEqual(len(set(ids)), 24)
        for batch in batches:
            batch_ids = [message["message_id"] for message in batch]
            self.assertEqual(batch_ids, sorted(batch_ids))

        for conversation_id, pair in ((room, batches[:2]), (dm_id, batches[2:])):
            with self.subTest(conversation_id=conversation_id):
                expected = sorted(pair[0] + pair[1], key=lambda message: message["message_id"])
                cursor = None
                for start in (8, 4, 0):
                    page = await self.storage.history(
                        bob["user_id"], conversation_id, before_id=cursor, limit=4
                    )
                    self.assertEqual(page["messages"], expected[start:start + 4])
                    cursor = page["next_before_id"]
                    self.assertEqual(cursor, expected[start]["message_id"] if start else None)
                    if start == 8:
                        await self.storage.write_message(alice["user_id"], conversation_id, "new arrival")

    async def test_latest_page_includes_highest_protocol_message_id(self):
        alice = await self.storage.register("Alice", "test-password")
        user_id = alice["user_id"]
        room = p.GENERAL_CONVERSATION_ID
        first = await self.storage.write_message(user_id, room, "first")
        # Advance the allocator in this temporary database to reach the boundary in two writes.
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "UPDATE sqlite_sequence SET seq = ? WHERE name = 'messages'", (p.MAX_MESSAGE_ID - 1,)
            )
        last = await self.storage.write_message(user_id, room, "last representable ID")
        self.assertEqual(last["message_id"], p.MAX_MESSAGE_ID)
        self.assertEqual(
            await self.storage.history(user_id, room, limit=1),
            {"messages": [last], "next_before_id": p.MAX_MESSAGE_ID},
        )
        self.assertEqual(
            await self.storage.history(user_id, room, before_id=p.MAX_MESSAGE_ID, limit=1),
            {"messages": [first], "next_before_id": None},
        )
        self.assertEqual(
            await self.storage.history(user_id, room, before_id=p.MIN_MESSAGE_ID),
            {"messages": [], "next_before_id": None},
        )
