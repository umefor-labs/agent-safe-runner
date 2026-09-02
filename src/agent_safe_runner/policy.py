from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import PolicyViolation


def _resolved_program(value: str, cwd: str | None = None) -> str | None:
    candidate = Path(value)
    if candidate.is_absolute() or os.path.dirname(value):
        candidate = Path(cwd or ".") / candidate
        return os.path.normcase(str(candidate.resolve())) if candidate.is_file() else None
    located = shutil.which(value)
    return os.path.normcase(str(Path(located).resolve())) if located else None


@dataclass(frozen=True)
class CommandRule:
    program: str
    args_prefix: tuple[str, ...] = ()

    def matches(self, command: tuple[str, ...], cwd: str | None = None) -> bool:
        expected_program = _resolved_program(self.program)
        if not command or expected_program is None or _resolved_program(command[0], cwd) != expected_program:
            return False
        return command[1 : 1 + len(self.args_prefix)] == self.args_prefix


@dataclass(frozen=True)
class Policy:
    rules: tuple[CommandRule, ...] = ()
    allowed_roots: tuple[Path, ...] = ()
    denied_arguments: tuple[str, ...] = ()
    environment_allowlist: tuple[str, ...] = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
    max_timeout_seconds: int = 300
    max_output_chars: int = 8000

    @classmethod
    def deny_all(cls) -> "Policy":
        return cls()

    @classmethod
    def from_file(cls, path: str | Path) -> "Policy":
        policy_path = Path(path)
        try:
            payload = json.loads(policy_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls.deny_all()
        except json.JSONDecodeError as exc:
            raise PolicyViolation(f"invalid policy JSON: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise PolicyViolation("unsupported policy version")
        try:
            rules = tuple(
                CommandRule(str(rule["program"]), tuple(str(arg) for arg in rule.get("args_prefix", [])))
                for rule in payload.get("allowed_commands", [])
            )
            roots = tuple(
                (policy_path.parent / Path(root)).resolve(strict=False)
                if not Path(root).is_absolute()
                else Path(root).resolve(strict=False)
                for root in payload.get("allowed_working_roots", [])
            )
            max_timeout = int(payload.get("max_timeout_seconds", 300))
            max_output = int(payload.get("max_output_chars", 8000))
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise PolicyViolation("invalid policy fields") from exc
        if not 1 <= max_timeout <= 86400 or not 256 <= max_output <= 1_000_000:
            raise PolicyViolation("policy limits are out of bounds")
        return cls(
            rules=rules,
            allowed_roots=roots,
            denied_arguments=tuple(str(item).casefold() for item in payload.get("denied_arguments", [])),
            environment_allowlist=tuple(str(item) for item in payload.get("environment_allowlist", cls.environment_allowlist)),
            max_timeout_seconds=max_timeout,
            max_output_chars=max_output,
        )

    @staticmethod
    def sample() -> dict[str, Any]:
        return {
            "version": 1,
            "allowed_commands": [
                {"program": "python", "args_prefix": ["--version"]},
                {"program": "python", "args_prefix": ["-m", "pytest"]},
                {"program": "git", "args_prefix": ["status"]},
            ],
            "allowed_working_roots": ["."],
            "denied_arguments": ["--force", "--hard"],
            "environment_allowlist": ["PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"],
            "max_timeout_seconds": 300,
            "max_output_chars": 8000,
        }

    def authorize(self, command: tuple[str, ...], cwd: str | None, timeout: int) -> CommandRule:
        if timeout > self.max_timeout_seconds:
            raise PolicyViolation(f"timeout exceeds policy limit of {self.max_timeout_seconds}s")
        matched = next((rule for rule in self.rules if rule.matches(command, cwd)), None)
        if matched is None:
            raise PolicyViolation("command does not match an allowlisted program and argument prefix")
        lowered_args = tuple(arg.casefold() for arg in command[1:])
        for denied in self.denied_arguments:
            if any(denied in arg for arg in lowered_args):
                raise PolicyViolation(f"command contains denied argument: {denied}")
        resolved_cwd = Path(cwd or ".").resolve(strict=False)
        if not self.allowed_roots:
            raise PolicyViolation("policy has no allowed working roots")
        if not any(
            resolved_cwd == root or resolved_cwd.is_relative_to(root) for root in self.allowed_roots
        ):
            raise PolicyViolation("working directory is outside allowed roots")
        return matched

    def environment(self) -> dict[str, str]:
        allowed = {name.casefold() for name in self.environment_allowlist}
        return {key: value for key, value in os.environ.items() if key.casefold() in allowed}
