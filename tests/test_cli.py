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
