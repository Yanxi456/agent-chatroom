"""Validate and normalize version 1 client requests."""

import re

from shared import protocol as p


_ENVELOPE_FIELDS = {"v", "type", "request_id", "data"}
_REQUEST_ID = re.compile(
    rf"[A-Za-z0-9_-]{{{p.MIN_REQUEST_ID_LENGTH},{p.MAX_REQUEST_ID_LENGTH}}}"
)
_USERNAME = re.compile(rf"[A-Za-z0-9_]{{{p.MIN_USERNAME_LENGTH},{p.MAX_USERNAME_LENGTH}}}")

_REQUEST_FIELDS = {
    p.AUTH_REGISTER: ({"username", "password"}, set()),
    p.AUTH_LOGIN: ({"username", "password"}, set()),
    p.AUTH_LOGOUT: (set(), set()),
    p.USERS_ONLINE: (set(), set()),
    p.CONVERSATION_LIST: (set(), set()),
    p.CONVERSATION_OPEN_DM: ({"peer_username"}, set()),
    p.MESSAGE_SEND: ({"conversation_id", "text"}, set()),
    p.HISTORY_LIST: ({"conversation_id"}, {"before_id", "limit", "keyword"}),
    p.PING: (set(), set()),
    p.AGENT_RUN: ({"conversation_id", "instruction"}, {"mode"}),
}


class UnusableRequest(ValueError):
    """The connection must close because the version or request ID is unusable."""


class RequestError(ValueError):
    """A request error that can be returned to the client."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def request_id(request: dict) -> str:
    version = request.get("v")
    if type(version) is not int or version != p.PROTOCOL_VERSION:
        raise UnusableRequest("Unsupported protocol version")
    value = request.get("request_id")
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        raise UnusableRequest("Invalid request ID")
    return value


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise RequestError(p.INVALID_ARGUMENT, "Expected a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RequestError(p.INVALID_ARGUMENT, "Invalid Unicode string") from exc
    return value


def _username(value: object) -> str:
    if not isinstance(value, str) or not _USERNAME.fullmatch(value):
        raise RequestError(p.INVALID_ARGUMENT, "Invalid username")
    return value


def _password(value: object) -> str:
    password = _string(value)
    if not p.MIN_PASSWORD_LENGTH <= len(password) <= p.MAX_PASSWORD_LENGTH:
        raise RequestError(p.INVALID_ARGUMENT, "Invalid password length")
    return password


def trimmed_text(value: object, max_length: int) -> str:
    text = _string(value).strip()
    if not text or len(text) > max_length:
        raise RequestError(p.INVALID_ARGUMENT, "Invalid text length")
    return text


def history_limit(value: object) -> int:
    if type(value) is not int or not p.MIN_HISTORY_LIMIT <= value <= p.MAX_HISTORY_LIMIT:
        raise RequestError(p.INVALID_ARGUMENT, "Invalid history limit")
    return value


def message_id(value: object) -> int:
    if type(value) is not int or not p.MIN_MESSAGE_ID <= value <= p.MAX_MESSAGE_ID:
        raise RequestError(p.INVALID_ARGUMENT, "Invalid message ID")
    return value


def _mode(value: object) -> str:
    if not isinstance(value, str) or value not in p.AGENT_MODES:
        raise RequestError(p.INVALID_ARGUMENT, "Invalid Agent mode")
    return value


_FIELD_VALIDATORS = {
    "username": _username,
    "peer_username": _username,
    "password": _password,
    "conversation_id": _string,
    "text": lambda value: trimmed_text(value, p.MAX_TEXT_LENGTH),
    "instruction": lambda value: trimmed_text(value, p.MAX_TEXT_LENGTH),
    "keyword": lambda value: trimmed_text(value, p.MAX_KEYWORD_LENGTH),
    "before_id": message_id,
    "limit": history_limit,
    "mode": _mode,
}


def parse_request(request: dict) -> tuple[str, dict]:
    """Check and normalize the request after its version and ID are accepted."""
    if request.keys() != _ENVELOPE_FIELDS:
        raise RequestError(p.INVALID_ARGUMENT, "Unexpected or missing request fields")

    name = request["type"]
    data = request["data"]
    if not isinstance(name, str) or not isinstance(data, dict):
        raise RequestError(p.INVALID_ARGUMENT, "Invalid request type or data")
    if name not in _REQUEST_FIELDS:
        raise RequestError(p.UNKNOWN_TYPE, "Unknown request type")

    required, optional = _REQUEST_FIELDS[name]
    if not required <= data.keys() <= required | optional:
        raise RequestError(p.INVALID_ARGUMENT, "Unexpected or missing data fields")
    return name, {field: _FIELD_VALIDATORS[field](value) for field, value in data.items()}
