import asyncio
import tempfile
import unittest
from pathlib import Path

from server.storage import Storage, StorageError

GENERAL = "room:general"
PASSWORD = "pässword-长-123"


class StorageTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "chatroom.sqlite3"
        self.storage = Storage(self.database)
        await self.storage.initialize()
        self.alice = await self.storage.register("Alice", PASSWORD)
        self.bob = await self.storage.register("Bob", PASSWORD)
        self.carol = await self.storage.register("Carol", PASSWORD)

    async def test_user_credentials_and_messages_persist(self):
        self.assertEqual(self.alice["username"], "alice")
        with self.assertRaises(StorageError) as error:
            await self.storage.register("ALICE", PASSWORD)
        self.assertEqual(error.exception.code, "USERNAME_TAKEN")

        original = await self.storage.write_message(self.alice["user_id"], GENERAL, "earlier")
        reopened = Storage(self.database)
        await reopened.initialize()
        self.assertEqual(await reopened.authenticate("ALICE", PASSWORD), self.alice)
        with self.assertRaises(StorageError) as error:
            await reopened.authenticate("ALICE", PASSWORD.upper())
        self.assertEqual(error.exception.code, "INVALID_CREDENTIALS")
        self.assertEqual(
            (await reopened.history(self.alice["user_id"], GENERAL))["messages"], [original]
        )
        later = await reopened.write_message(self.alice["user_id"], GENERAL, "later")
        self.assertGreater(later["message_id"], original["message_id"])
        self.assertIsNone(later["task_id"])

    async def test_dm_is_idempotent_under_concurrent_open_and_is_member_only(self):
        alice_id, bob_id, carol_id = self.alice["user_id"], self.bob["user_id"], self.carol["user_id"]
        alice_dm, bob_dm, repeated_dm = await asyncio.gather(
            self.storage.open_dm(alice_id, "BOB"),
            self.storage.open_dm(bob_id, "Alice"),
            self.storage.open_dm(alice_id, "bob"),
        )
        dm_id = alice_dm["conversation_id"]
        self.assertEqual(alice_dm, {"conversation_id": dm_id, "kind": "dm", "peer": self.bob})
        self.assertEqual(bob_dm, {"conversation_id": dm_id, "kind": "dm", "peer": self.alice})
        self.assertEqual(repeated_dm, alice_dm)
        self.assertCountEqual(
            await self.storage.list_conversations(alice_id),
            [{"conversation_id": GENERAL, "kind": "group", "peer": None}, alice_dm],
        )
        self.assertEqual(
            await self.storage.list_conversations(carol_id),
            [{"conversation_id": GENERAL, "kind": "group", "peer": None}],
        )

        public = await self.storage.write_message(alice_id, GENERAL, "public")
        private = await self.storage.write_message(alice_id, dm_id, "private")
        self.assertGreater(private["message_id"], public["message_id"])
        self.assertEqual((await self.storage.history(bob_id, dm_id))["messages"], [private])
        self.assertEqual((await self.storage.history(carol_id, GENERAL))["messages"], [public])
        for operation, arguments in (
            (self.storage.check_access, (carol_id, dm_id)),
            (self.storage.history, (carol_id, dm_id)),
            (self.storage.write_message, (carol_id, dm_id, "forbidden")),
        ):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(StorageError) as error:
                    await operation(*arguments)
                self.assertEqual(error.exception.code, "FORBIDDEN")

    async def test_history_filters_before_pagination_and_keeps_ascii_case_rules(self):
        user_id = self.alice["user_id"]
        stored = [
            await self.storage.write_message(user_id, GENERAL, body)
            for body in ("old KEY_%", "keyAX", "middle key_%", "ÄKey_%", "new KeY_%", "äkeyAX")
        ]
        latest = await self.storage.history(user_id, GENERAL, keyword="kEy_%", limit=2)
        self.assertEqual(latest["messages"], stored[3:5])
        self.assertEqual(latest["next_before_id"], stored[3]["message_id"])
        earlier = await self.storage.history(
            user_id, GENERAL, keyword="kEy_%", limit=2, before_id=latest["next_before_id"]
        )
        self.assertEqual(earlier, {"messages": [stored[0], stored[2]], "next_before_id": None})
        self.assertEqual(
            await self.storage.history(user_id, GENERAL, keyword="äKEY_%"),
            {"messages": [], "next_before_id": None},
        )
        self.assertEqual(
            (await self.storage.history(user_id, GENERAL, keyword="ÄkEY_%"))["messages"],
            [stored[3]],
        )

    async def test_history_applies_the_stricter_exclusive_id_bound(self):
        user_id = self.alice["user_id"]
        messages = [
            await self.storage.write_message(user_id, GENERAL, text)
            for text in ("first", "second", "trigger", "later")
        ]
        second, trigger, later = (message["message_id"] for message in messages[1:])
        for before, maximum, expected in (
            (None, trigger, messages[:2]),
            (later, trigger, messages[:2]),
            (second, trigger, messages[:1]),
        ):
            with self.subTest(before=before, maximum=maximum):
                self.assertEqual(
                    await self.storage.history(
                        user_id, GENERAL, before_id=before, max_id_exclusive=maximum
                    ),
                    {"messages": expected, "next_before_id": None},
                )
