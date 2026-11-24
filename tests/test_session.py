import asyncio
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.main import ChatServer, main
from server.storage import Storage
from shared.codec import FrameDecoder, encode_frame

PASSWORD = "demo-password"


class Connection:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.next_id = 1

    async def send(self, message_type, data, **extra):
        request_id = f"r{self.next_id}"
        self.next_id += 1
        frame = encode_frame(
            {"v": 1, "type": message_type, "request_id": request_id, "data": data, **extra}
        )
        self.writer.write(frame)
        await self.writer.drain()
        return request_id

    async def receive(self):
        async with asyncio.timeout(3):
            header = await self.reader.readexactly(4)
            size, = struct.unpack("!I", header)
            frame = header + await self.reader.readexactly(size)
        message, = FrameDecoder().feed(frame)
        return message

    async def close(self):
        self.writer.close()
        await self.writer.wait_closed()


class SessionTest(unittest.IsolatedAsyncioTestCase):
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

    async def connect(self):
        connection = Connection(*await asyncio.open_connection(*self.address))
        self.addAsyncCleanup(connection.close)
        return connection

    async def request(self, connection, message_type, data, **extra):
        request_id = await connection.send(message_type, data, **extra)
        response = await connection.receive()
        self.assertEqual(response["type"], "response")
        self.assertEqual(response["request_id"], request_id)
        self.assertEqual(response["v"], 1)
        return response["data"]

    async def assert_error(self, connection, message_type, data, code, **extra):
        response = await self.request(connection, message_type, data, **extra)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], code)

    async def login(self, connection, username):
        result = await self.request(
            connection, "auth.login", {"username": username, "password": PASSWORD}
        )
        self.assertTrue(result["ok"])
        user = result["result"]["user"]
        event = await connection.receive()
        self.assertEqual(event["type"], "presence.changed")
        self.assertNotIn("request_id", event)
        self.assertIn(user, event["data"]["users"])
        return user

    async def test_auth_state_login_response_order_and_logout(self):
        await self.start()
        first, second = await self.connect(), await self.connect()
        registration = {"username": "ALICE", "password": PASSWORD}
        await self.assert_error(first, "users.online", {}, "AUTH_REQUIRED")
        await self.assert_error(first, "auth.logout", {}, "AUTH_REQUIRED")
        response = await self.request(first, "auth.register", registration)
        self.assertEqual(response["result"]["user"]["username"], "alice")
        await self.assert_error(first, "users.online", {}, "AUTH_REQUIRED")
        self.assertEqual(await self.request(first, "ping", {}), {"ok": True, "result": {}})
        self.assertEqual(await self.login(first, "Alice"), response["result"]["user"])
        await self.assert_error(first, "auth.register", registration, "INVALID_STATE")
        await self.assert_error(first, "auth.login", registration, "INVALID_STATE")
        await self.assert_error(second, "auth.login", registration, "ALREADY_ONLINE")

        self.assertEqual(await self.request(first, "auth.logout", {}), {"ok": True, "result": {}})
        self.assertEqual(await asyncio.wait_for(first.reader.read(1), 3), b"")
        await self.login(second, "alice")

    async def test_session_stays_open_after_structured_validation_errors(self):
        await self.start()
        peer = await self.connect()
        for request, data, code, extra in (
            ("unrecognized", {}, "UNKNOWN_TYPE", {}),
            ("unrecognized", {}, "INVALID_ARGUMENT", {"extra": True}),
            ("ping", {}, "INVALID_ARGUMENT", {"extra": True}),
            ("ping", {"extra": True}, "INVALID_ARGUMENT", {}),
            ("ping", [], "INVALID_ARGUMENT", {}),
            (45, {}, "INVALID_ARGUMENT", {}),
            ("auth.register", {"username": "AgEnT", "password": PASSWORD}, "INVALID_ARGUMENT", {}),
            ("auth.login", {"username": "AgEnT", "password": PASSWORD}, "INVALID_CREDENTIALS", {}),
        ):
            with self.subTest(request=request, data=data, extra=extra):
                await self.assert_error(peer, request, data, code, **extra)
        self.assertEqual(await self.request(peer, "ping", {}), {"ok": True, "result": {}})

    async def test_invalid_envelopes_and_fatal_lengths_close_connection(self):
        await self.start()
        frames = (
            encode_frame({"v": 1, "type": "ping", "data": {}}),
            encode_frame({"v": 1, "type": "ping", "data": {}, "request_id": "a b"}),
            encode_frame({"v": 2, "type": "ping", "data": {}, "request_id": "r"}),
            encode_frame({"v": True, "type": "ping", "data": {}, "request_id": "r"}),
            struct.pack("!I", 0),
            struct.pack("!I", 1_048_577),
        )
        for frame in frames:
            with self.subTest(frame=frame):
                peer = await self.connect()
                peer.writer.write(frame)
                await peer.writer.drain()
                self.assertEqual(await asyncio.wait_for(peer.reader.read(1), 3), b"")

    async def test_aborted_socket_releases_only_its_login_and_pushes_presence(self):
        await self.start()
        alice, bob = await self.connect(), await self.connect()
        await self.storage.register("alice", PASSWORD)
        await self.storage.register("bob", PASSWORD)
        alice_user = await self.login(alice, "alice")
        bob_user = await self.login(bob, "bob")
        event = await alice.receive()
        self.assertEqual(event["type"], "presence.changed")
        self.assertCountEqual(event["data"]["users"], [alice_user, bob_user])

        bob.writer.transport.abort()
        event = await alice.receive()
        self.assertEqual(event["type"], "presence.changed")
        self.assertEqual(event["data"]["users"], [alice_user])
        self.assertEqual(await self.login(await self.connect(), "bob"), bob_user)

    async def test_heartbeat_timeout_closes_socket_and_releases_login(self):
        await self.start(idle_timeout=0.5)
        await self.storage.register("alice", PASSWORD)
        alice = await self.connect()
        user = await self.login(alice, "alice")
        for _ in range(3):
            await asyncio.sleep(0.2)
            self.assertEqual(await self.request(alice, "ping", {}), {"ok": True, "result": {}})
        self.assertEqual(await asyncio.wait_for(alice.reader.read(1), 3), b"")
        self.assertEqual(await self.login(await self.connect(), "alice"), user)

    async def test_cancelling_main_closes_connected_client(self):
        listening = asyncio.get_running_loop().create_future()
        start = ChatServer.start

        async def start_on_loopback(server):
            listener = await start(server, "127.0.0.1", 0)
            listening.set_result(listener)
            return listener

        with (
            patch("server.main.config.DB_PATH", self.storage.path),
            patch.object(ChatServer, "start", start_on_loopback),
            patch("server.main.logging.basicConfig"),
        ):
            service = asyncio.create_task(main())

            async def stop_service():
                service.cancel()
                await asyncio.gather(service, return_exceptions=True)

            self.addAsyncCleanup(stop_service)
            listener = await asyncio.wait_for(listening, 3)
            self.address = listener.sockets[0].getsockname()[:2]
            peer = await self.connect()
            self.assertEqual(await self.request(peer, "ping", {}), {"ok": True, "result": {}})

            service.cancel()
            # Timing out here must not cancel the service a second time.
            done, _ = await asyncio.wait({service}, timeout=3)
            self.assertIn(service, done, "Shutdown waited for the client to disconnect")
            self.assertTrue(service.cancelled())
            self.assertFalse(listener.is_serving())
            self.assertEqual(await asyncio.wait_for(peer.reader.read(1), 3), b"")
