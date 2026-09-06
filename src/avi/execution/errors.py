"""Exceptions for the command execution subsystem."""


class ExecutionError(Exception):
    """Base exception for command execution failures."""


class CommandTimeoutError(ExecutionError):
    """Raised when command execution exceeds configured timeout limit."""


class CommandNotFoundError(ExecutionError):
    """Raised when the specified executable is not found on the system."""


class PermissionDeniedError(ExecutionError):
    """Raised when command execution is rejected due to insufficient permissions."""


class InvalidCommandError(ExecutionError):
    """Raised when the command request is malformed or invalid."""
