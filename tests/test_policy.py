import json

import pytest

from agent_safe_runner.errors import PolicyViolation
from agent_safe_runner.policy import Policy


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 1.0),
    ("allowed_commands", None), ("allowed_commands", "python"),
    ("allowed_commands", [{"program": 12}]),
    ("allowed_commands", [{"program": ""}]),
    ("allowed_commands", [{"program": "python", "args_prefix": "--version"}]),
    ("allowed_commands", [{"program": "python", "args_prefix": [1]}]),
    ("allowed_commands", [{"program": "python", "arg_prefix": ["--version"]}]),
    ("allowed_working_roots", "."), ("allowed_working_roots", [None]),
    ("allowed_working_roots", [""]), ("allowed_working_roots", ["bad\x00path"]),
    ("denied_arguments", None), ("denied_arguments", "--force"),
    ("denied_arguments", [False]), ("environment_allowlist", None),
    ("environment_allowlist", "PATH"), ("environment_allowlist", [1]),
    ("max_timeout_seconds", True), ("max_timeout_seconds", "30"),
    ("max_timeout_seconds", 1.5), ("max_output_chars", "8000"),
    ("max_output_chars", 8000.5), ("max_output_chars", float("inf")),
    ("unknown_option", True),
])
def test_rejects_malformed_policy_fields(tmp_path, field, value):
    payload = Policy.sample()
    payload[field] = value
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PolicyViolation):
        Policy.from_file(path)


def test_rejects_duplicate_keys_without_echoing_input(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"version":1,"private-marker":1,"private-marker":2}', encoding="utf-8")
    with pytest.raises(PolicyViolation) as error:
        Policy.from_file(path)
    assert "private-marker" not in str(error.value)


def test_accepts_utf8_bom_and_resolves_roots_relative_to_policy(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(Policy.sample()), encoding="utf-8-sig")
    policy = Policy.from_file(path)
    assert policy.allowed_roots == (tmp_path.resolve(),)
    assert policy.rules[0].args_prefix == ("--version",)


def test_missing_policy_still_denies_by_default(tmp_path):
    assert Policy.from_file(tmp_path / "missing.json") == Policy.deny_all()


def test_minimal_policy_and_empty_argument_remain_valid(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"version":1}', encoding="utf-8")
    assert Policy.from_file(path) == Policy.deny_all()
    payload = Policy.sample()
    payload["allowed_commands"][0]["args_prefix"] = [""]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert Policy.from_file(path).rules[0].args_prefix == ("",)
