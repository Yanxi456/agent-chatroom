"""Environment configuration for the TCP server."""

import os

from shared.protocol import DEFAULT_HOST, DEFAULT_PORT


HOST = os.getenv("SERVER_HOST", DEFAULT_HOST)
PORT = int(os.getenv("SERVER_PORT", str(DEFAULT_PORT)))
DB_PATH = os.getenv("SERVER_DB_PATH", "chatroom.sqlite3")
