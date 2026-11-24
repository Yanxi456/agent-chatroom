"""Encode and decode length-prefixed JSON objects for the TCP protocol."""

import json
from struct import Struct

from shared.protocol import FRAME_HEADER_SIZE, MAX_FRAME_LENGTH, MIN_FRAME_LENGTH


_HEADER = Struct("!I")


class FrameError(ValueError):
    """A frame cannot be encoded or decoded under the TCP protocol."""


def _check_length(length: int) -> None:
    if not MIN_FRAME_LENGTH <= length <= MAX_FRAME_LENGTH:
        raise FrameError("Frame length is outside the allowed range")


def _reject_constant(value: str) -> None:
    raise ValueError("Non-JSON numeric constant")


def encode_frame(message: dict) -> bytes:
    if not isinstance(message, dict):
        raise FrameError("Frame payload must be a JSON object")
    try:
        payload = json.dumps(
            message, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise FrameError("Frame payload cannot be encoded as UTF-8 JSON") from exc
    _check_length(len(payload))
    return _HEADER.pack(len(payload)) + payload


class FrameDecoder:
    """Accumulate bytes until each complete frame is available."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._payload_length: int | None = None

    def feed(self, chunk: bytes) -> list[dict]:
        self._buffer.extend(chunk)
        messages = []
        offset = 0
        while True:
            if self._payload_length is None:
                if len(self._buffer) - offset < FRAME_HEADER_SIZE:
                    break
                (length,) = _HEADER.unpack_from(self._buffer, offset)
                _check_length(length)
                self._payload_length = length
                offset += FRAME_HEADER_SIZE

            end = offset + self._payload_length
            if end > len(self._buffer):
                break
            try:
                payload = self._buffer[offset:end].decode("utf-8")
                message = json.loads(payload, parse_constant=_reject_constant)
            except (ValueError, RecursionError) as exc:
                raise FrameError("Invalid UTF-8 JSON payload") from exc
            if not isinstance(message, dict):
                raise FrameError("Frame payload must be a JSON object")
            messages.append(message)
            self._payload_length = None
            offset = end

        del self._buffer[:offset]
        return messages
