import json
import sys

import pytest

from agent_safe_runner import __version__
from agent_safe_runner.cli import main


def test_cli_policy_init_and_queue_lifecycle(tmp_path, capsys):
    policy = tmp_path / "policy.json"
    database = tmp_path / "jobs.sqlite3"
    audit = tmp_path / "audit.jsonl"
    assert main(["init-policy", str(policy)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "created"

    common = ["--db", str(database), "--audit", str(audit), "--policy", str(policy)]
    assert main([*common, "submit", "--cwd", str(tmp_path), "--", "git", "status"]) == 0
    job = json.loads(capsys.readouterr().out)
    assert job["status"] == "queued"

    assert main([*common, "show", job["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == job["id"]

    assert main([*common, "cancel", job["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "cancelled"


def test_cli_returns_structured_error(tmp_path, capsys):
    result = main(["--db", str(tmp_path / "jobs.sqlite3"), "show", "missing"])
    payload = json.loads(capsys.readouterr().err)
    assert result == 2
    assert payload["error"]["code"] == "job_not_found"


def test_cli_reports_version(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"agent-safe {__version__}"


def test_sample_policy_allows_first_run_python_version(tmp_path, capsys):
    policy = tmp_path / "policy.json"
    database = tmp_path / "jobs.sqlite3"
    audit = tmp_path / "audit.jsonl"
    assert main(["init-policy", str(policy)]) == 0
    capsys.readouterr()

    common = ["--db", str(database), "--audit", str(audit), "--policy", str(policy)]
    assert main([*common, "submit", "--cwd", str(tmp_path), "--", "python", "--version"]) == 0
    job = json.loads(capsys.readouterr().out)
    assert main([*common, "approve", job["id"], "--by", "operator"]) == 0
    assert json.loads(capsys.readouterr().out)["approval_status"] == "approved"
    assert main([*common, "run", job["id"], "--execute"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "succeeded"


def test_check_policy_is_read_only(tmp_path, capsys):
    from agent_safe_runner.policy import Policy

    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(Policy.sample()), encoding="utf-8")
    before = policy.read_bytes()
    assert main(["--db", str(tmp_path / "missing" / "jobs.sqlite3"),
                 "--audit", str(tmp_path / "audit.jsonl"),
                 "--policy", str(policy), "check-policy"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["valid"] is True
    assert output["rule_count"] == 3
    assert policy.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["policy.json"]


def test_check_policy_missing_is_error_without_creating_files(tmp_path, capsys):
    assert main(["--policy", str(tmp_path / "missing.json"), "check-policy"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "policy_denied"
    assert list(tmp_path.iterdir()) == []


def test_invalid_policy_fails_before_queue_creation(tmp_path, capsys):
    policy = tmp_path / "policy.json"
    policy.write_text('{"version":1,"denied_arguments":null}', encoding="utf-8")
    database = tmp_path / "jobs.sqlite3"
    assert main(["--policy", str(policy), "--db", str(database), "list"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "policy_denied"
    assert not database.exists()


def test_corrupt_database_returns_safe_structured_error(tmp_path, capsys):
    database = tmp_path / "jobs.sqlite3"
    database.write_bytes(b"not a sqlite database" * 100)
    before = database.read_bytes()
    assert main(["--db", str(database), "--policy", str(tmp_path / "missing.json"), "list"]) == 2
    output = json.loads(capsys.readouterr().err)
    assert output["error"]["code"] == "storage_error"
    assert database.read_bytes() == before
