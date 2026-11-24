import unittest
from bisect import bisect_right
from itertools import accumulate

from shared.codec import FrameDecoder, FrameError, encode_frame
from shared.protocol import FRAME_HEADER_SIZE, MAX_FRAME_LENGTH


class FrameBoundaryTest(unittest.TestCase):
    def test_multibyte_text_at_and_one_byte_over_wire_limit(self):
        symbol = "🙂"
        overhead = len(b'{"text":""}')
        count, remainder = divmod(
            MAX_FRAME_LENGTH - overhead, len(symbol.encode("utf-8"))
        )
        text = "a" * remainder + symbol * count
        frame = encode_frame({"text": text})
        self.assertEqual(len(frame), FRAME_HEADER_SIZE + MAX_FRAME_LENGTH)
        header = frame[:FRAME_HEADER_SIZE]
        self.assertEqual(int.from_bytes(header, "big"), MAX_FRAME_LENGTH)

        # Split the last four-byte symbol, then finish this frame and the next together.
        decoder = FrameDecoder()
        self.assertEqual(decoder.feed(frame[:-4]), [])
        decoded = decoder.feed(frame[-4:] + encode_frame({}))
        self.assertEqual(decoded, [{"text": text}, {}])
        with self.assertRaises(FrameError):
            encode_frame({"text": text + "a"})

    def test_each_read_emits_exactly_the_completed_frames_in_a_batch(self):
        messages = [{"id": i, "text": "中文🙂" * (i % 13)} for i in range(256)]
        frames = [encode_frame(message) for message in messages]
        stream = b"".join(frames)
        frame_ends = list(accumulate(map(len, frames)))

        for read_size in (3, 127, 8192):
            with self.subTest(read_size=read_size):
                decoder = FrameDecoder()
                emitted = 0
                for start in range(0, len(stream), read_size):
                    end = start + read_size
                    completed = bisect_right(frame_ends, end)
                    decoded = decoder.feed(stream[start:end])
                    self.assertEqual(decoded, messages[emitted:completed])
                    emitted = completed
                self.assertEqual(emitted, len(messages))
