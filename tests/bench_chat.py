"""Run with python -m unittest discover -s tests -p bench_chat.py."""

import asyncio
import math
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path
from statistics import median
from time import perf_counter

from server.main import ChatServer
from server.storage import Storage
from test_integration import ChatClient, GENERAL, PASSWORD


class ChatWriteBenchmark(unittest.TestCase):
    def test_group_writes(self):
        for client_count in (1, 8):
            with self.subTest(client_count=client_count):
                asyncio.run(self.measure_writes(client_count))

    async def measure_writes(self, client_count):
        total = 128
        with tempfile.TemporaryDirectory() as directory:
            async with AsyncExitStack() as cleanup:
                server = ChatServer(Storage(Path(directory) / "chat.sqlite3"))
                listener = await server.start("127.0.0.1", 0)
                cleanup.push_async_callback(server.close)
                address = listener.sockets[0].getsockname()[:2]
                clients = []
                for index in range(client_count):
                    client = ChatClient(*await asyncio.open_connection(*address))
                    cleanup.push_async_callback(client.close)
                    credentials = {"username": f"bench_{index}", "password": PASSWORD}
                    for operation in ("auth.register", "auth.login"):
                        reply = await client.request(operation, **credentials)
                        self.assertTrue(reply["ok"], reply)
                    clients.append(client)

                # Drain login and presence traffic before timing the first send.
                for client in clients:
                    self.assertTrue((await client.request("ping"))["ok"])
                    client.pending.clear()

                latencies = []
                sent = [[] for _ in clients]
                received = [[] for _ in clients]

                def collect_events(index, client):
                    for event in client.pending_events("message.new"):
                        received[index].append(event["message"]["message_id"])
                    client.pending.clear()

                async def send_batch(index, client):
                    for sequence in range(total // client_count):
                        body = f"群聊 {index}/{sequence}"
                        start = perf_counter()
                        reply = await client.request("message.send", conversation_id=GENERAL, text=body)
                        latencies.append((perf_counter() - start) * 1000)
                        self.assertTrue(reply["ok"], reply)
                        message = reply["result"]["message"]
                        self.assertEqual(message["text"], body)
                        sent[index].append(message["message_id"])
                        collect_events(index, client)

                start = perf_counter()
                async with asyncio.TaskGroup() as group:
                    for index, client in enumerate(clients):
                        group.create_task(send_batch(index, client))
                elapsed = perf_counter() - start

                # A later ping response follows every broadcast already enqueued.
                all_ids = {mid for sender_ids in sent for mid in sender_ids}
                self.assertEqual(len(all_ids), total)
                for index, client in enumerate(clients):
                    self.assertTrue((await client.request("ping"))["ok"])
                    collect_events(index, client)
                    self.assertCountEqual(received[index], all_ids.difference(sent[index]))

                p95 = sorted(latencies)[math.ceil(0.95 * len(latencies)) - 1]
                print(f"clients={client_count} sends={total} writes/s={total / elapsed:.1f} "
                      f"p50_ms={median(latencies):.2f} p95_ms={p95:.2f}")
