import struct
import unittest

from shared.codec import FrameDecoder, FrameError, encode_frame
from shared.protocol import MAX_FRAME_LENGTH


def raw_frame(payload: bytes) -> bytes:
    return struct.pack("!I", len(payload)) + payload


class FrameCodecTest(unittest.TestCase):
    def test_encodes_utf8_json_with_big_endian_byte_length(self):
        frame = encode_frame({"text": "你好"})
        self.assertEqual(struct.unpack("!I", frame[:4])[0], len(frame[4:]))
        self.assertEqual(FrameDecoder().feed(frame), [{"text": "你好"}])

    def test_decodes_coalesced_frames_and_retains_partial_tail(self):
        first, second, third = [{"number": index} for index in range(3)]
        third_frame = encode_frame(third)
        decoder = FrameDecoder()
        self.assertEqual(
            decoder.feed(encode_frame(first) + encode_frame(second) + third_frame[:6]),
            [first, second],
        )
        self.assertEqual(decoder.feed(third_frame[6:]), [third])

    def test_decodes_frames_split_at_every_byte(self):
        message = {"text": "截断🙂"}
        decoder = FrameDecoder()
        frame = encode_frame(message)
        for byte in frame[:-1]:
            self.assertEqual(decoder.feed(bytes([byte])), [])
        self.assertEqual(decoder.feed(frame[-1:]), [message])

    def test_rejects_invalid_lengths_as_soon_as_header_completes(self):
        for length in (0, MAX_FRAME_LENGTH + 1):
            with self.subTest(length=length):
                header = struct.pack("!I", length)
                decoder = FrameDecoder()
                self.assertEqual(decoder.feed(header[:3]), [])
                with self.assertRaises(FrameError):
                    decoder.feed(header[3:])

    def test_rejects_invalid_utf8_json_and_non_object_roots(self):
        payloads = (b'{"text":"\xff"}', b'{"value":', b'{"value":NaN}', b'[]', b'null')
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(FrameError):
                    FrameDecoder().feed(raw_frame(payload))

    def test_accepts_a_payload_at_the_frame_limit(self):
        text_size = MAX_FRAME_LENGTH - len(b'{"text":""}')
        payload = b'{"text":"' + b'a' * text_size + b'"}'
        frame = raw_frame(payload)
        decoded, = FrameDecoder().feed(frame)
        self.assertEqual(len(decoded["text"]), text_size)

    def test_encoder_rejects_oversize_and_non_object_payloads(self):
        with self.assertRaises(FrameError):
            encode_frame({"text": "A" * MAX_FRAME_LENGTH})
        with self.assertRaises(FrameError):
            encode_frame([])
