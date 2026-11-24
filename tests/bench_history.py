"""Run with python -B -m unittest discover -s tests -p bench_history.py."""

import asyncio
import math
import sqlite3
import statistics
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from server.storage import Storage
from shared import protocol as p


WARMUPS = 3
SAMPLES = 50


class HistoryBenchmark(unittest.TestCase):
    def test_history_latency(self):
        for size in (1_000, 50_000):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as directory:
                asyncio.run(self.measure_history(Path(directory) / "chatroom.sqlite3", size))

    async def measure_history(self, path, size):
        storage = Storage(path)
        await storage.initialize()
        user = await storage.register("reader", "test-password")
        user_id = user["user_id"]
        room = p.GENERAL_CONVERSATION_ID
        with closing(sqlite3.connect(path)) as db, db:
            db.executemany(
                """INSERT INTO messages (conversation_id, sender_id, kind, text, created_at_ms)
                   VALUES (?, ?, 'user', ?, ?)""",
                ((room, user_id, f"Status update for ticket {i}", i) for i in range(1, size + 1)),
            )

        cursor = size // 2 + 1
        page_ids = list(range(cursor - p.DEFAULT_HISTORY_LIMIT, cursor))
        cases = (
            ("cursor_page", {"before_id": cursor}, page_ids, page_ids[0]),
            ("keyword_miss", {"keyword": "no-such-ticket"}, [], None),
        )
        for name, options, expected_ids, expected_cursor in cases:
            samples_ms = []
            for iteration in range(WARMUPS + SAMPLES):
                start = time.perf_counter_ns()
                result = await storage.history(user_id, room, **options)
                elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
                self.assertEqual([message["message_id"] for message in result["messages"]], expected_ids)
                self.assertEqual(result["next_before_id"], expected_cursor)
                if iteration >= WARMUPS:
                    samples_ms.append(elapsed_ms)

            p95_ms = sorted(samples_ms)[math.ceil(0.95 * SAMPLES) - 1]
            print(
                f"rows={size} query={name} samples={SAMPLES} warmups={WARMUPS} "
                f"p50={statistics.median(samples_ms):.3f}ms p95={p95_ms:.3f}ms",
                flush=True,
            )
