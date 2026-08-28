from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import SensitiveInputError


REDACTED = "<redacted>"
_SENSITIVE_KEYS = re.compile(r"(?i)(api[_-]?key|authorization|cookie|credential|password|secret|session|token)")
_KNOWN_TOKEN = re.compile(r"(?i)\b(sk-[a-z0-9_-]{12,}|gh[opusr]_[a-z0-9]{20,}|bearer\s+[a-z0-9._~+/=-]{12,})")
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|authorization|cookie|credential|password|secret|session|token)\s*[:=]\s*([^\s,;]+)"
)
_SENSITIVE_FLAGS = {
    "--api-key",
    "--authorization",
    "--cookie",
    "--credentials",
    "--password",
    "--secret",
    "--session",
    "--token",
}


def reject_sensitive_command(command: Sequence[str]) -> None:
    """Reject secrets before they can be persisted in SQLite or audit logs."""
    for raw_arg in command:
        arg = str(raw_arg)
        lowered = arg.casefold()
        if lowered in _SENSITIVE_FLAGS:
            raise SensitiveInputError("secret-like command arguments are not accepted; use an external secret provider")
        if any(lowered.startswith(flag + "=") for flag in _SENSITIVE_FLAGS):
            raise SensitiveInputError("secret-like command arguments are not accepted; use an external secret provider")
        if _KNOWN_TOKEN.search(arg) or _ASSIGNMENT.search(arg):
            raise SensitiveInputError("secret-like command arguments are not accepted; use an external secret provider")


def redact_text(value: str) -> str:
    value = _KNOWN_TOKEN.sub(REDACTED, value)
    return _ASSIGNMENT.sub(lambda match: f"{match.group(1)}={REDACTED}", value)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _SENSITIVE_KEYS.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [redact(item) for item in value]
    return value
