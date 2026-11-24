import asyncio
import tempfile
import unittest
from pathlib import Path

from agent.errors import AgentUnavailableError, ToolError
from server.main import ChatServer
from server.storage import Storage
from shared.codec import encode_frame
from test_session import PASSWORD, Connection

GENERAL = "room:general"


class ChatClient(Connection):
    def __init__(self, reader, writer):
        super().__init__(reader, writer)
        self.pending = []

    async def _take(self, matches):
        for index, frame in enumerate(self.pending):
            if matches(frame):
                return self.pending.pop(index)
        async with asyncio.timeout(3):
            while True:
                frame = await self.receive()
                if matches(frame):
                    return frame
                self.pending.append(frame)

    async def response(self, request_id):
        frame = await self._take(
            lambda frame: frame["type"] == "response" and frame["request_id"] == request_id
        )
        return frame["data"]

    async def request(self, message_type, **data):
        return await self.response(await self.send(message_type, data))

    async def event(self, event_type, **fields):
        frame = await self._take(
            lambda frame: frame["type"] == event_type
            and all(frame["data"].get(key) == value for key, value in fields.items())
        )
        return frame["data"]

    def pending_events(self, event_type):
        return [frame["data"] for frame in self.pending if frame["type"] == event_type]


class ChatIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.storage = Storage(Path(directory.name) / "chatroom.sqlite3")
        await self.storage.initialize()

    async def start(self, **options):
        server = ChatServer(self.storage, **options)
        listener = await server.start("127.0.0.1", 0)
        self.addAsyncCleanup(server.close)
        self.address = listener.sockets[0].getsockname()[:2]
        return server

    async def connect(self):
        client = ChatClient(*await asyncio.open_connection(*self.address))
        self.addAsyncCleanup(client.close)
        return client

    async def result(self, client, message_type, **data):
        response = await client.request(message_type, **data)
        self.assertTrue(response["ok"], response)
        return response["result"]

    async def assert_error(self, client, code, message_type, **data):
        response = await client.request(message_type, **data)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], code)

    async def register(self, client, username):
        result = await self.result(client, "auth.register", username=username, password=PASSWORD)
        return result["user"]

    async def login(self, client, username):
        result = await self.result(client, "auth.login", username=username, password=PASSWORD)
        return result["user"]

    async def peers(self):
        clients = [await self.connect() for _ in range(3)]
        for client, username in zip(clients, ("alice", "bob", "carol"), strict=True):
            await self.register(client, username)
            await self.login(client, username)
        return clients

    async def test_group_dm_and_conversation_privacy(self):
        await self.start()
        alice, bob, carol = [await self.connect() for _ in range(3)]
        alice_user = await self.register(alice, "Alice")
        bob_user = await self.register(bob, "Bob")
        carol_user = await self.register(carol, "Carol")
        await self.login(alice, "alice")

        dm = (await self.result(alice, "conversation.open_dm", peer_username="BOB"))["conversation"]
        dm_id = dm["conversation_id"]
        self.assertEqual(dm, {"conversation_id": dm_id, "kind": "dm", "peer": bob_user})
        self.assertEqual(
            await self.result(alice, "conversation.open_dm", peer_username="bob"),
            {"conversation": dm},
        )
        await self.assert_error(alice, "INVALID_ARGUMENT", "conversation.open_dm", peer_username="ALICE")
        await self.assert_error(alice, "USER_NOT_FOUND", "conversation.open_dm", peer_username="unknown")

        await self.login(bob, "bob")
        await self.login(carol, "carol")
        online = (await self.result(alice, "users.online"))["users"]
        self.assertCountEqual(online, [alice_user, bob_user, carol_user])
        public = {"conversation_id": GENERAL, "kind": "group", "peer": None}
        self.assertCountEqual(
            (await self.result(alice, "conversation.list"))["conversations"], [public, dm]
        )
        self.assertCountEqual(
            (await self.result(bob, "conversation.list"))["conversations"],
            [public, {"conversation_id": dm_id, "kind": "dm", "peer": alice_user}],
        )
        self.assertEqual((await self.result(carol, "conversation.list"))["conversations"], [public])

        sent = await self.result(alice, "message.send", conversation_id=GENERAL, text="  @Agent 普通聊天  ")
        public_message = sent["message"]
        self.assertEqual(public_message["text"], "@Agent 普通聊天")
        self.assertEqual(public_message["kind"], "user")
        self.assertIsNone(public_message["task_id"])
        self.assertEqual(public_message["sender"], alice_user)
        for client in (bob, carol):
            self.assertEqual(await client.event("message.new"), {"message": public_message})
        self.assertEqual(await self.result(alice, "ping"), {})
        self.assertEqual(alice.pending_events("message.new"), [])

        sent = await self.result(bob, "message.send", conversation_id=dm_id, text="私聊消息")
        private_message = sent["message"]
        self.assertEqual(private_message["sender"], bob_user)
        self.assertEqual(await alice.event("message.new"), {"message": private_message})
        self.assertEqual(await self.result(bob, "ping"), {})
        self.assertEqual(bob.pending_events("message.new"), [])
        await self.assert_error(carol, "FORBIDDEN", "history.list", conversation_id=dm_id)
        await self.assert_error(carol, "FORBIDDEN", "message.send", conversation_id=dm_id, text="blocked")
        self.assertEqual(carol.pending_events("message.new"), [])
        self.assertEqual(
            await self.result(alice, "history.list", conversation_id=dm_id, keyword="消息"),
            {"messages": [private_message], "next_before_id": None},
        )

    async def test_repeated_send_id_has_no_second_write_and_is_scoped_to_connection(self):
        await self.start()
        alice, bob = await self.connect(), await self.connect()
        for client, username in ((alice, "alice"), (bob, "bob")):
            await self.register(client, username)
            await self.login(client, username)

        request_id = "send-1"
        frame = encode_frame({
            "v": 1, "type": "message.send", "request_id": request_id,
            "data": {"conversation_id": GENERAL, "text": "same message"},
        })
        alice.writer.write(frame + frame)
        await alice.writer.drain()
        replies = [await alice.response(request_id) for _ in range(2)]
        accepted = [reply["result"]["message"] for reply in replies if reply["ok"]]
        errors = [reply["error"]["code"] for reply in replies if not reply["ok"]]
        self.assertEqual(len(accepted), 1, replies)
        self.assertEqual(errors, ["INVALID_ARGUMENT"])
        alice_message = accepted[0]

        bob.writer.write(frame)
        await bob.writer.drain()
        second = await bob.response(request_id)
        self.assertTrue(second["ok"], second)
        bob_message = second["result"]["message"]
        self.assertGreater(bob_message["message_id"], alice_message["message_id"])
        self.assertEqual(await alice.event("message.new"), {"message": bob_message})
        self.assertEqual(await bob.event("message.new"), {"message": alice_message})
        for client in (alice, bob):
            page = await self.result(client, "history.list", conversation_id=GENERAL)
            self.assertEqual(page, {"messages": [alice_message, bob_message], "next_before_id": None})
            self.assertEqual(client.pending_events("message.new"), [])

    async def test_history_literal_pagination_after_relogin(self):
        await self.start()
        alice, bob, _ = await self.peers()
        dm = (await self.result(alice, "conversation.open_dm", peer_username="bob"))["conversation"]
        stored = {GENERAL: [], dm["conversation_id"]: []}
        for conversation_id, messages in stored.items():
            for body in ("first SET_%", "match false positive setXY", "second set_%", "last SeT_%"):
                sent = await self.result(
                    alice, "message.send", conversation_id=conversation_id, text=body
                )
                messages.append(sent["message"])

        await self.result(bob, "auth.logout")
        self.assertEqual(await asyncio.wait_for(bob.reader.read(1), 3), b"")
        bob = await self.connect()
        await self.login(bob, "bob")
        for conversation_id, messages in stored.items():
            with self.subTest(conversation_id=conversation_id):
                filters = {"conversation_id": conversation_id, "keyword": "  sEt_%  ", "limit": 2}
                newer = await self.result(bob, "history.list", **filters)
                self.assertEqual(newer, {
                    "messages": messages[2:], "next_before_id": messages[2]["message_id"]
                })
                older = await self.result(bob, "history.list", **filters, before_id=newer["next_before_id"])
                self.assertEqual(older, {"messages": [messages[0]], "next_before_id": None})
        await self.assert_error(bob, "INVALID_ARGUMENT", "history.list", conversation_id=GENERAL, limit=True)

    async def test_fragmented_and_coalesced_pings_are_matched_independently(self):
        await self.start()
        client, bob, carol = await self.peers()
        first, second, third = [
            encode_frame({"v": 1, "type": "ping", "request_id": request_id, "data": {}})
            for request_id in ("first", "second", "third")
        ]
        for piece in (first[:2], first[2:9], first[9:] + second + third):
            client.writer.write(piece)
            await client.writer.drain()
            await asyncio.sleep(0.02)

        expected = {"ok": True, "result": {}}
        self.assertEqual(await bob.request("ping"), expected)
        self.assertEqual(await carol.request("ping"), expected)
        self.assertEqual(await client.response("third"), expected)
        self.assertEqual(await client.response("first"), expected)
        self.assertEqual(await client.response("second"), expected)

    async def test_agent_tools_see_only_pretrigger_history_while_chat_continues(self):
        read_now = asyncio.Event()
        respond_now = asyncio.Event()
        used_pages = asyncio.Queue()
        call = {"call_id": "history-1", "name": "read_history"}

        async def run_task(context, tools, emit):
            await emit({**call, "state": "started"})
            await read_now.wait()
            page = await tools["read_history"]()
            await emit({**call, "state": "succeeded", "result_count": len(page["messages"])})
            used_pages.put_nowait((context, page))
            await respond_now.wait()
            return {"text": "  总结完成  "}

        await self.start(run_task=run_task)
        alice, bob, carol = await self.peers()
        before = (await self.result(alice, "message.send", conversation_id=GENERAL, text="演示计划"))[
            "message"
        ]
        accepted = await self.result(
            alice, "agent.run", conversation_id=GENERAL, instruction="  总结计划  ", mode="summary"
        )
        task_id, trigger = accepted["task_id"], accepted["message"]
        self.assertEqual(trigger["text"], "@Agent 总结计划")
        self.assertEqual(trigger["task_id"], task_id)
        self.assertEqual(alice.pending_events("agent.task"), [])
        for client in (bob, carol):
            await client.event("message.new", message=trigger)

        running = await alice.event("agent.task", task_id=task_id)
        self.assertEqual(running, {"task_id": task_id, "conversation_id": GENERAL, "state": "running"})
        started = await alice.event("agent.tool", task_id=task_id)
        self.assertEqual(
            started, {**call, "task_id": task_id, "conversation_id": GENERAL, "state": "started"}
        )

        later = (await self.result(bob, "message.send", conversation_id=GENERAL, text="任务后新消息"))[
            "message"
        ]
        await alice.event("message.new", message=later)
        await carol.event("message.new", message=later)
        self.assertEqual(await self.result(alice, "ping"), {})
        read_now.set()

        finished = await alice.event("agent.tool", task_id=task_id, state="succeeded")
        self.assertEqual(
            finished,
            {**call, "task_id": task_id, "conversation_id": GENERAL, "state": "succeeded", "result_count": 1},
        )
        context, page = await asyncio.wait_for(used_pages.get(), 3)
        self.assertEqual(context["trigger_message_id"], trigger["message_id"])
        self.assertEqual(context["instruction"], "总结计划")
        self.assertEqual(context["mode"], "summary")
        self.assertEqual(page, {"messages": [before], "next_before_id": None})

        respond_now.set()
        first_terminal_frame = await alice._take(
            lambda frame: (
                frame["type"] == "message.new" and frame["data"]["message"]["task_id"] == task_id
            )
            or (
                frame["type"] == "agent.task"
                and frame["data"]["task_id"] == task_id
                and frame["data"]["state"] != "running"
            )
        )
        self.assertEqual(first_terminal_frame["type"], "message.new")
        reply = first_terminal_frame["data"]["message"]
        self.assertEqual(reply["text"], "总结完成")
        self.assertEqual(reply["kind"], "agent")
        self.assertEqual(reply["sender"], {"user_id": "agent", "username": "Agent"})
        terminal = await alice.event("agent.task", task_id=task_id, state="succeeded")
        self.assertEqual(terminal["message_id"], reply["message_id"])
        for client in (bob, carol):
            await client.event("message.new", message=reply)

    async def test_agent_private_runs_and_search_tools_respect_conversation_membership(self):
        observed_pages = asyncio.Queue()
        call = {"call_id": "search-1", "name": "search_history"}

        async def run_task(context, tools, emit):
            await emit({**call, "state": "started"})
            page = await tools["search_history"](keyword="  NeEdLe  ")
            await emit({**call, "state": "succeeded", "result_count": len(page["messages"])})
            observed_pages.put_nowait((context, page))
            return {"text": "私聊总结"}

        await self.start(run_task=run_task)
        alice, bob, carol = await self.peers()
        dm = (await self.result(alice, "conversation.open_dm", peer_username="bob"))["conversation"]
        dm_id = dm["conversation_id"]
        group = (await self.result(alice, "message.send", conversation_id=GENERAL, text="group needle"))[
            "message"
        ]
        await carol.event("message.new", message=group)
        dm_message = (await self.result(bob, "message.send", conversation_id=dm_id, text="DM needle"))[
            "message"
        ]
        await self.assert_error(
            carol, "FORBIDDEN", "agent.run", conversation_id=dm_id, instruction="outside needle"
        )

        accepted = await self.result(alice, "agent.run", conversation_id=dm_id, instruction="总结 needle")
        task_id = accepted["task_id"]
        await bob.event("message.new", message=accepted["message"])
        for state in ("started", "succeeded"):
            event = await alice.event("agent.tool", task_id=task_id, state=state)
            self.assertEqual(event["call_id"], call["call_id"])
            self.assertEqual(event["conversation_id"], dm_id)
        terminal = await alice.event("agent.task", task_id=task_id, state="succeeded")
        reply = await bob._take(
            lambda frame: frame["type"] == "message.new"
            and frame["data"]["message"]["message_id"] == terminal["message_id"]
        )
        self.assertEqual(reply["data"]["message"]["conversation_id"], dm_id)
        self.assertEqual(reply["data"]["message"]["text"], "私聊总结")
        context, observed = await asyncio.wait_for(observed_pages.get(), 3)
        self.assertEqual(context["conversation_id"], dm_id)
        self.assertEqual(observed, {"messages": [dm_message], "next_before_id": None})

        for client in (bob, carol):
            await self.result(client, "ping")
            self.assertEqual(client.pending_events("agent.tool"), [])
            self.assertEqual(client.pending_events("agent.task"), [])
        self.assertEqual(carol.pending_events("message.new"), [])

    async def test_model_and_tool_failures_release_task_capacity_without_interrupting_chat(self):
        call = {"call_id": "invalid-search", "name": "search_history"}

        async def run_task(context, tools, emit):
            if context["instruction"] == "model unavailable":
                raise AgentUnavailableError()
            await emit({**call, "state": "started"})
            try:
                await tools["search_history"](keyword="   ")
            except ToolError as error:
                await emit({
                    **call,
                    "state": "failed",
                    "error": {"code": error.code, "message": error.message},
                })
                raise
            return {"text": "unexpected success"}

        await self.start(run_task=run_task)
        alice, bob, carol = await self.peers()
        unavailable = await self.result(
            alice, "agent.run", conversation_id=GENERAL, instruction="model unavailable"
        )
        failed = await alice.event("agent.task", task_id=unavailable["task_id"], state="failed")
        self.assertEqual(failed["error"]["code"], "AGENT_UNAVAILABLE")

        tool_failure = await self.result(
            alice, "agent.run", conversation_id=GENERAL, instruction="invalid search"
        )
        task_id = tool_failure["task_id"]
        await alice.event("agent.tool", task_id=task_id, state="started", call_id=call["call_id"])
        tool_error = await alice.event("agent.tool", task_id=task_id, state="failed", call_id=call["call_id"])
        self.assertEqual(tool_error["error"]["code"], "INVALID_ARGUMENT")
        terminal = await alice.event("agent.task", task_id=task_id, state="failed")
        self.assertEqual(terminal["error"]["code"], "AGENT_FAILED")

        public = (await self.result(carol, "message.send", conversation_id=GENERAL, text="继续聊天"))[
            "message"
        ]
        for client in (alice, bob):
            await client.event("message.new", message=public)
        dm = (await self.result(alice, "conversation.open_dm", peer_username="bob"))["conversation"]
        private = (await self.result(
            alice, "message.send", conversation_id=dm["conversation_id"], text="继续私聊"
        ))["message"]
        await bob.event("message.new", message=private)
        history = await self.result(alice, "history.list", conversation_id=GENERAL)
        self.assertEqual(history["messages"], [unavailable["message"], tool_failure["message"], public])

    async def test_requester_disconnect_does_not_prevent_reply_and_history_recovery(self):
        proceed = asyncio.Event()
        call = {"call_id": "offline-history", "name": "read_history"}

        async def run_task(_context, tools, emit):
            await proceed.wait()
            await emit({**call, "state": "started"})
            page = await tools["read_history"]()
            await emit({**call, "state": "succeeded", "result_count": len(page["messages"])})
            return {"text": "断线后完成"}

        await self.start(run_task=run_task)
        alice, bob, carol = await self.peers()
        accepted = await self.result(alice, "agent.run", conversation_id=GENERAL, instruction="继续处理")
        task_id, trigger = accepted["task_id"], accepted["message"]
        await alice.event("agent.task", task_id=task_id, state="running")
        alice.writer.transport.abort()
        await bob._take(
            lambda frame: frame["type"] == "presence.changed"
            and {user["username"] for user in frame["data"]["users"]} == {"bob", "carol"}
        )
        proceed.set()

        await bob.event("message.new", message=trigger)
        reply_event = await bob.event("message.new")
        reply = reply_event["message"]
        self.assertEqual(reply["text"], "断线后完成")
        self.assertEqual(reply["task_id"], task_id)
        self.assertEqual(reply["kind"], "agent")
        await carol.event("message.new", message=reply)
        for client in (bob, carol):
            await self.result(client, "ping")
            self.assertEqual(client.pending_events("agent.tool"), [])
            self.assertEqual(client.pending_events("agent.task"), [])

        reconnected = await self.connect()
        await self.login(reconnected, "alice")
        history = await self.result(reconnected, "history.list", conversation_id=GENERAL)
        self.assertEqual(history, {"messages": [trigger, reply], "next_before_id": None})
