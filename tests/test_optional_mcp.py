import importlib.util
import json
import subprocess
import sys

import pytest


def test_core_import_does_not_load_mcp():
    result = subprocess.run([sys.executable, "-c", "import sys; import agent_safe_runner.cli; assert 'mcp' not in sys.modules"],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(importlib.util.find_spec("mcp") is not None, reason="only for the core-only installation")
def test_missing_optional_dependency_is_actionable_and_does_not_pollute_stdout(tmp_path):
    result = subprocess.run([sys.executable, "-m", "agent_safe_runner", "--db", str(tmp_path / "db.sqlite3"),
                             "--audit", str(tmp_path / "audit.jsonl"), "--policy", str(tmp_path / "policy.json"), "mcp"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"] == "optional_dependency_missing"
    assert not (tmp_path / "db.sqlite3").exists()
