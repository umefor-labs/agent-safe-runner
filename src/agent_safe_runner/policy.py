from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import PolicyViolation


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyViolation("policy JSON contains duplicate keys")
        result[key] = value
    return result


def _string_list(value: Any, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or "\x00" in item or (not item and not allow_empty)
        for item in value
    ):
        raise PolicyViolation(f"{field} must be an array of valid strings")
    return tuple(value)


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
    def from_file(cls, path: str | Path, *, allow_missing: bool = True) -> "Policy":
        policy_path = Path(path)
        try:
            payload = json.loads(policy_path.read_text(encoding="utf-8-sig"),
                                 object_pairs_hook=_unique_object)
        except FileNotFoundError:
            if not allow_missing:
                raise PolicyViolation("policy file does not exist") from None
            return cls.deny_all()
        except json.JSONDecodeError as exc:
            raise PolicyViolation(f"invalid policy JSON at line {exc.lineno}, column {exc.colno}") from exc
        except UnicodeError as exc:
            raise PolicyViolation("policy file must be UTF-8 encoded") from exc
        if not isinstance(payload, dict) or type(payload.get("version")) is not int or payload["version"] != 1:
            raise PolicyViolation("unsupported policy version")
        if set(payload) - {"version", "allowed_commands", "allowed_working_roots",
                           "denied_arguments", "environment_allowlist", "max_timeout_seconds", "max_output_chars"}:
            raise PolicyViolation("policy contains unknown fields; check field names")
        raw_rules = payload.get("allowed_commands", [])
        if not isinstance(raw_rules, list):
            raise PolicyViolation("allowed_commands must be an array of rule objects")
        rules = []
        for rule in raw_rules:
            if not isinstance(rule, dict) or set(rule) - {"program", "args_prefix"}:
                raise PolicyViolation("command rules must be objects with only program and args_prefix")
            program = rule.get("program")
            if not isinstance(program, str) or not program or "\x00" in program:
                raise PolicyViolation("rule program must be a nonempty string without NUL characters")
            rules.append(CommandRule(program, _string_list(rule.get("args_prefix", []),
                                                           "args_prefix", allow_empty=True)))
        raw_roots = _string_list(payload.get("allowed_working_roots", []), "allowed_working_roots")
        denied = _string_list(payload.get("denied_arguments", []), "denied_arguments")
        environment = _string_list(payload.get("environment_allowlist", list(cls.environment_allowlist)),
                                   "environment_allowlist")
        max_timeout = payload.get("max_timeout_seconds", 300)
        max_output = payload.get("max_output_chars", 8000)
        if type(max_timeout) is not int or type(max_output) is not int:
            raise PolicyViolation("policy limits must be integers, not strings, booleans, or decimals")
        if not 1 <= max_timeout <= 86400 or not 256 <= max_output <= 1_000_000:
            raise PolicyViolation("policy limits are out of bounds")
        try:
            roots = tuple(
                (policy_path.parent / Path(root)).resolve(strict=False)
                if not Path(root).is_absolute()
                else Path(root).resolve(strict=False)
                for root in raw_roots
            )
        except (OSError, ValueError) as exc:
            raise PolicyViolation("invalid policy working roots") from exc
        return cls(
            rules=tuple(rules),
            allowed_roots=roots,
            denied_arguments=tuple(item.casefold() for item in denied),
            environment_allowlist=environment,
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
