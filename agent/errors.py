"""Exceptions exchanged between the backend and the Agent runner."""

from typing import Literal


class ToolError(Exception):
    def __init__(
        self,
        code: Literal["INVALID_ARGUMENT", "FORBIDDEN", "INTERNAL_ERROR"],
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AgentUnavailableError(Exception):
    """The Agent cannot reach a configured model."""


class AgentExecutionError(Exception):
    """The Agent was unable to complete the task."""
