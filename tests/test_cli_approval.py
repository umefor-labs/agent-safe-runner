import json
import subprocess
import sys

import pytest

from agent_safe_runner import __version__
from agent_safe_runner.cli import main


def test_installed_module_approval_workflow(tmp_path):
    """Use separate processes outside the checkout, also exercised by CI's wheel install."""
    def run(*args, exitcode=0):
        result = subprocess.run([sys.executable, "-m", "agent_safe_runner", *args],
                                cwd=tmp_path, text=True, capture_output=True, timeout=30)
        assert result.returncode == exitcode, result.stderr
        return json.loads(result.stdout if exitcode == 0 else result.stderr)

    run("init-policy")
    job = run("submit", "--cwd", ".", "--timeout", "30", "--", "python", "--version")
    job_id = job["id"]
    assert job["approval_status"] == "pending"
    assert run("inbox")[0]["id"] == job_id
    assert run("list", "--approval", "pending")[0]["id"] == job_id
    assert run("run", job_id, "--execute", exitcode=2)["error"]["code"] == "approval_required"
    assert run("work", "--once", "--execute")["status"] == "idle"
    assert run("run", job_id)["approval_status"] == "pending"
    assessment = run("assess", job_id)
    assert assessment["allowed"] is True
    assert assessment["execution_ready"] is False
    assert run("approve", job_id, "--by", "operator", "--reason", "Reviewed")["approval_status"] == "approved"
    assert run("approve", job_id, "--by", "second", exitcode=2)["error"]["code"] == "invalid_job_state"
    assert run("inbox") == []
    finished = run("work", "--once", "--execute", "--worker", "smoke-test")
    assert finished["id"] == job_id
    assert finished["status"] == "succeeded"
    assert "Python" in finished["result"]["stdout"]
    assert run("audit-verify")["valid"] is True

    rejected = run("submit", "--key", "unneeded", "--", "python", "--version")
    assert run("deny", rejected["id"], "--by", "operator", "--reason", "Not needed")["status"] == "cancelled"
    assert run("list", "--approval", "denied")[0]["id"] == rejected["id"]
    assert run("retry", rejected["id"])["approval_status"] == "pending"
    assert run("audit-verify")["valid"] is True


@pytest.mark.parametrize("args", [("approve", "id"), ("deny", "id", "--by", "operator")])
def test_decision_cli_requires_actor_and_denial_reason(args):
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_package_metadata_version_matches_runtime():
    from importlib.metadata import version

    assert version("agent-safe-runner") == __version__
