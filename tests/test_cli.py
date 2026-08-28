import json
import sys

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
