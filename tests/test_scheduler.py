import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from server.main import ChatServer
from server.storage import Storage
from test_integration import GENERAL, ChatClient
from test_session import PASSWORD


class SchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.storage = Storage(Path(directory.name) / "chatroom.sqlite3")
        await self.storage.initialize()

    async def start(self, runner, **options):
        server = ChatServer(self.storage, run_task=runner, **options)
        listener = await server.start("127.0.0.1", 0)
        self.addAsyncCleanup(server.close)
        self.address = listener.sockets[0].getsockname()[:2]

    async def connect(self, username):
        client = ChatClient(*await asyncio.open_connection(*self.address))
        self.addAsyncCleanup(client.close)
        credentials = {"username": username, "password": PASSWORD}
        for request in ("auth.register", "auth.login"):
            response = await client.request(request, **credentials)
            self.assertTrue(response["ok"], response)
        return client

    async def submit(self, client, instruction):
        response = await client.request(
            "agent.run", conversation_id=GENERAL, instruction=instruction
        )
        self.assertTrue(response["ok"], response)
        return response["result"]["task_id"]

    async def assert_submit_failure(self, client, instruction, expected_code):
        response = await client.request(
            "agent.run", conversation_id=GENERAL, instruction=instruction
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], expected_code)

    async def history(self, client):
        response = await client.request("history.list", conversation_id=GENERAL)
        self.assertTrue(response["ok"], response)
        return response["result"]["messages"]

    async def test_global_and_user_capacity_fifo_and_busy_does_not_write(self):
        instructions = [f"job{index}" for index in range(6)]
        releases = {instruction: asyncio.Event() for instruction in instructions}
        started = asyncio.Queue()

        async def run_task(context, _tools, _emit):
            instruction = context["instruction"]
            started.put_nowait(instruction)
            await releases[instruction].wait()
            return {"text": f"done {instruction}"}

        await self.start(run_task)
        clients = [await self.connect(f"user{index}") for index in range(7)]
        first = await self.submit(clients[0], instructions[0])
        await self.assert_submit_failure(clients[0], "duplicate job", "AGENT_BUSY")
        task_ids = [first]
        for index, instruction in enumerate(instructions[1:], start=1):
            task_ids.append(await self.submit(clients[index], instruction))
        await self.assert_submit_failure(clients[6], "seventh job", "AGENT_BUSY")

        for index in range(2):
            self.assertEqual(await asyncio.wait_for(started.get(), 3), instructions[index])
            await clients[index].event("agent.task", task_id=task_ids[index], state="running")
        self.assertTrue(started.empty())
        history = await self.history(clients[6])
        self.assertEqual(
            [message["text"] for message in history],
            [f"@Agent {instruction}" for instruction in instructions],
        )

        previous = 0
        for index in range(2, 6):
            releases[instructions[previous]].set()
            await clients[previous].event(
                "agent.task", task_id=task_ids[previous], state="succeeded"
            )
            self.assertEqual(await asyncio.wait_for(started.get(), 3), instructions[index])
            await clients[index].event("agent.task", task_id=task_ids[index], state="running")
            self.assertTrue(started.empty())
            previous = index

        for index in (1, 5):
            releases[instructions[index]].set()
            await clients[index].event("agent.task", task_id=task_ids[index], state="succeeded")

    async def test_waiting_time_is_excluded_and_late_runner_cannot_emit_or_write(self):
        timeout = 0.7
        release_late = asyncio.Event()
        late_returning = asyncio.Event()
        release_queued = asyncio.Event()

        async def run_task(context, _tools, emit):
            instruction = context["instruction"]
            if instruction == "late":
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    await release_late.wait()
                    await emit({"call_id": "late", "name": "read_history", "state": "started"})
                    late_returning.set()
                    return {"text": "late result"}
            if instruction == "slow":
                await asyncio.Event().wait()
            if instruction == "queued":
                await release_queued.wait()
            return {"text": f"done {instruction}"}

        await self.start(run_task, agent_timeout=timeout)
        alice, bob, carol = [await self.connect(name) for name in ("alice", "bob", "carol")]
        late = await self.submit(alice, "late")
        slow = await self.submit(bob, "slow")
        queued = await self.submit(carol, "queued")
        loop = asyncio.get_running_loop()
        accepted_at = loop.time()

        for client, task_id in ((alice, late), (bob, slow)):
            failed = await client.event("agent.task", task_id=task_id, state="failed")
            self.assertEqual(failed["error"]["code"], "AGENT_TIMEOUT")
        await carol.event("agent.task", task_id=queued, state="running")
        follow_up = await self.submit(alice, "follow-up")
        await alice.event("agent.task", task_id=follow_up, state="succeeded")

        await asyncio.sleep(max(0, accepted_at + timeout + 0.08 - loop.time()))
        release_queued.set()
        await carol.event("agent.task", task_id=queued, state="succeeded")

        release_late.set()
        await asyncio.wait_for(late_returning.wait(), 3)
        recorded = await self.history(alice)
        self.assertFalse(
            any(message["kind"] == "agent" and message["task_id"] == late for message in recorded)
        )
        self.assertEqual(alice.pending_events("agent.tool"), [])
        late_terminals = [
            event
            for event in alice.pending_events("agent.task")
            if event["task_id"] == late and event["state"] != "running"
        ]
        self.assertEqual(late_terminals, [])

    async def test_result_past_deadline_is_discarded_after_event_loop_delay(self):
        async def run_task(_context, _tools, _emit):
            asyncio.get_running_loop().call_soon(time.sleep, 0.08)
            await asyncio.sleep(0.005)
            return {"text": "late reply"}

        await self.start(run_task, agent_timeout=0.03)
        client = await self.connect("alice")
        task_id = await self.submit(client, "deadline")

        failed = await client.event("agent.task", task_id=task_id, state="failed")
        self.assertEqual(failed["error"]["code"], "AGENT_TIMEOUT")
        self.assertEqual(
            [message["text"] for message in await self.history(client)], ["@Agent deadline"]
        )

    async def test_failed_trigger_write_does_not_push_and_returns_reservation(self):
        started = asyncio.Event()

        async def run_task(_context, _tools, _emit):
            started.set()
            return {"text": "done"}

        await self.start(run_task)
        alice = await self.connect("alice")
        bob = await self.connect("bob")
        with patch.object(self.storage, "write_message", side_effect=sqlite3.OperationalError):
            await self.assert_submit_failure(alice, "unwritten trigger", "INTERNAL_ERROR")
        self.assertEqual(await self.history(bob), [])
        self.assertEqual(bob.pending_events("message.new"), [])
        self.assertFalse(started.is_set())

        task_id = await self.submit(alice, "retry")
        await alice.event("agent.task", task_id=task_id, state="succeeded")
        self.assertTrue(started.is_set())
        self.assertEqual(
            [message["text"] for message in await self.history(alice)], ["@Agent retry", "done"]
        )

    async def test_result_validation_and_unicode_length_boundary(self):
        boundary = "🙂" * 2000
        invalid_results = {
            "empty": {"text": " \n\t "},
            "too long": {"text": boundary + "字"},
            "wrong type": {"text": 1},
            "extra field": {"text": "hello", "extra": True},
        }
        results = {**invalid_results, "at limit": {"text": "  " + boundary + "\n"}}

        async def run_task(context, _tools, _emit):
            return results[context["instruction"]]

        await self.start(run_task)
        client = await self.connect("alice")
        triggers = []
        for instruction in invalid_results:
            with self.subTest(instruction=instruction):
                task_id = await self.submit(client, instruction)
                triggers.append(task_id)
                await client.event("agent.task", task_id=task_id, state="running")
                terminal = await client.event("agent.task", task_id=task_id)
                self.assertEqual(terminal["state"], "failed")
                self.assertEqual(terminal["error"]["code"], "AGENT_FAILED")

        valid_task = await self.submit(client, "at limit")
        terminal = await client.event("agent.task", task_id=valid_task, state="succeeded")
        messages = await self.history(client)
        self.assertEqual([m["task_id"] for m in messages if m["kind"] == "user"], [*triggers, valid_task])
        replies = [m for m in messages if m["kind"] == "agent"]
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["message_id"], terminal["message_id"])
        self.assertEqual(replies[0]["task_id"], valid_task)
        self.assertEqual(replies[0]["text"], boundary)

    async def test_reply_write_failure_preserves_trigger_and_allows_retry(self):
        finish = asyncio.Event()

        async def run_task(_context, _tools, _emit):
            await finish.wait()
            return {"text": "done"}

        await self.start(run_task)
        client = await self.connect("alice")
        task_id = await self.submit(client, "first")
        await client.event("agent.task", task_id=task_id, state="running")
        with patch.object(self.storage, "write_message", side_effect=sqlite3.OperationalError):
            finish.set()
            failed = await client.event("agent.task", task_id=task_id, state="failed")
        self.assertEqual(failed["error"]["code"], "INTERNAL_ERROR")
        retry = await self.submit(client, "retry")
        await client.event("agent.task", task_id=retry, state="succeeded")
        self.assertEqual(
            [(m["task_id"], m["kind"]) for m in await self.history(client)],
            [(task_id, "user"), (retry, "user"), (retry, "agent")],
        )
        self.assertEqual(
            [e["message"]["task_id"] for e in client.pending_events("message.new")], [retry]
        )

    async def test_inflight_task_follows_user_across_logout_and_login(self):
        finish = asyncio.Event()
        call = {"call_id": "after-login", "name": "read_history"}

        async def run_task(_context, tools, emit):
            await finish.wait()
            await emit({**call, "state": "started"})
            page = await tools["read_history"]()
            await emit({**call, "state": "succeeded", "result_count": len(page["messages"])})
            return {"text": "done"}

        await self.start(run_task)
        original = await self.connect("alice")
        task_id = await self.submit(original, "follow me")
        await original.event("agent.task", task_id=task_id, state="running")
        logout = await original.request("auth.logout")
        self.assertTrue(logout["ok"], logout)
        self.assertEqual(await asyncio.wait_for(original.reader.read(1), 3), b"")

        current = ChatClient(*await asyncio.open_connection(*self.address))
        self.addAsyncCleanup(current.close)
        login = await current.request("auth.login", username="alice", password=PASSWORD)
        self.assertTrue(login["ok"], login)
        await self.assert_submit_failure(current, "while running", "AGENT_BUSY")
        finish.set()

        for state in ("started", "succeeded"):
            await current.event("agent.tool", task_id=task_id, call_id=call["call_id"], state=state)
        terminal = await current.event("agent.task", task_id=task_id)
        self.assertEqual(terminal["state"], "succeeded")
        reply = (await current.event("message.new"))["message"]
        self.assertEqual(reply["message_id"], terminal["message_id"])
        self.assertEqual(reply["task_id"], task_id)
        self.assertEqual([m["text"] for m in await self.history(current)], ["@Agent follow me", "done"])
