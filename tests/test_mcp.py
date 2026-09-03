import asyncio
import json
import sqlite3
import sys

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters, stdio_client
from agent_safe_runner.core import JobStore
from agent_safe_runner.mcp_server import ServerPaths, TOOL_SCHEMAS, _dispatch
from agent_safe_runner.policy import Policy


@pytest.fixture
def paths(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(Policy.sample()), encoding="utf-8")
    return ServerPaths.checked(str(tmp_path / "jobs.sqlite3"), str(tmp_path / "audit.jsonl"), str(policy))


def payload(result):
    return json.loads(result.content[0].text)


async def connect(paths, scenario):
    params = StdioServerParameters(command=sys.executable, args=[
        "-m", "agent_safe_runner", "--db", str(paths.db), "--audit", str(paths.audit),
        "--policy", str(paths.policy), "mcp",
    ], cwd=str(paths.db.parent))
    async with asyncio.timeout(45):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=15) as client:
                initialized = await client.initialize()
                await scenario(client, initialized)


def test_real_stdio_client_proposal_and_external_approval(paths, tmp_path):
    async def scenario(client, initialized):
        assert initialized.server_info.name == "agent-safe-runner"
        tools = (await client.list_tools()).tools
        assert {tool.name for tool in tools} == set(TOOL_SCHEMAS)
        for tool in tools:
            assert tool.input_schema["additionalProperties"] is False
            assert tool.annotations.read_only_hint == (tool.name != "submit_command")
            assert len(tool.description) >= 100
            assert all("description" in field for field in tool.input_schema["properties"].values())
        args = {"command": ["python", "--version"], "cwd": str(tmp_path)}
        proposed = await client.call_tool("submit_command", args)
        assert not proposed.is_error
        job = payload(proposed)
        assert job["source"] == "mcp"
        assert job["approval_status"] == "pending"
        assert job["attempts"] == 0
        assert payload(await client.call_tool("submit_command", args))["id"] == job["id"]
        assert payload(await client.call_tool("get_job", {"job_id": job["id"]}))["result"] is None
        assert payload(await client.call_tool("list_jobs", {"approval": "pending"}))[0]["id"] == job["id"]
        assessed = payload(await client.call_tool("assess_job", {"job_id": job["id"]}))
        assert assessed["allowed"] is True
        assert assessed["execution_ready"] is False
        # Only the separate operator API can approve and execute the proposal.
        with JobStore(paths.db, paths.audit, policy=Policy.from_file(paths.policy)) as operator:
            assert operator.work_once(execute=True) is None
            operator.approve(job["id"], by="integration-reviewer")
            assert operator.run(job["id"], execute=True).status == "succeeded"
        assert payload(await client.call_tool("get_job", {"job_id": job["id"]}))["status"] == "succeeded"
        assert payload(await client.call_tool("verify_audit", {}))["valid"] is True
    asyncio.run(connect(paths, scenario))


def test_mcp_rejects_unsafe_capabilities_and_bad_arguments(paths, tmp_path):
    async def scenario(client, initialized):
        for name in ("approve", "deny", "retry", "run", "execute", "cancel", "work"):
            result = await client.call_tool(name, {"job_id": "missing"})
            assert result.is_error
            assert payload(result)["error"]["code"] == "tool_not_found"
        invalid_arguments = [
            {"command": [], "cwd": str(tmp_path)},
            {"command": ["python"], "cwd": "."},
            {"command": ["python"], "cwd": str(tmp_path), "execute": True},
            {"command": ["python"], "cwd": str(tmp_path), "approval_status": "approved"},
            {"command": ["python"], "cwd": str(tmp_path), "source": "cli"},
            {"command": ["python"], "cwd": str(tmp_path), "timeout": True},
            {"command": ["python"], "cwd": str(tmp_path), "max_attempts": 0},
            {"command": ["python", "\x00"], "cwd": str(tmp_path)},
            {"command": ["python", *("x" * 4096 for _ in range(17))], "cwd": str(tmp_path)},
        ]
        for args in invalid_arguments:
            result = await client.call_tool("submit_command", args)
            assert result.is_error
            assert payload(result)["error"]["code"] == "invalid_input"
        assert payload(await client.call_tool("list_jobs", {})) == []
        for args in ({"limit": 101}, {"status": "whatever"}, {"db": str(tmp_path / "other.sqlite3")}):
            assert (await client.call_tool("list_jobs", args)).is_error
        secret = "sk-" + "abcdefghijklmnop"
        secret_result = await client.call_tool("submit_command", {"command": ["tool", "--token", secret], "cwd": str(tmp_path)})
        assert secret_result.is_error
        assert payload(secret_result)["error"]["code"] == "sensitive_input"
        assert secret not in str(secret_result)
        malformed = await client.call_tool("submit_command", {"secret": secret})
        assert secret not in str(malformed)
        missing = await client.call_tool("get_job", {"job_id": "missing"})
        assert missing.is_error
        assert payload(missing)["error"]["code"] == "job_not_found"
    asyncio.run(connect(paths, scenario))


def test_long_lived_server_reloads_policy_and_reports_tampering(paths, tmp_path):
    async def scenario(client, initialized):
        job = payload(await client.call_tool("submit_command", {"command": ["python", "--version"], "cwd": str(tmp_path)}))
        paths.policy.write_text(json.dumps({"version": 1}), encoding="utf-8")
        assert not payload(await client.call_tool("assess_job", {"job_id": job["id"]}))["allowed"]
        paths.audit.write_text(paths.audit.read_text(encoding="utf-8").replace('"source": "mcp"', '"source": "changed"'), encoding="utf-8")
        result = await client.call_tool("verify_audit", {})
        assert result.is_error
        assert payload(result)["error"]["code"] == "audit_integrity_error"
    asyncio.run(connect(paths, scenario))


def test_read_only_requests_do_not_migrate_or_reclaim_jobs(paths):
    with JobStore(paths.db, paths.audit) as store:
        job = store.submit(("python", "--version"))
        store._db.execute("UPDATE jobs SET status = 'running', approval_status = 'legacy', lease_until = 0 WHERE id = ?", (job.id,))
        store._db.commit()
    before = paths.audit.read_bytes()
    assert _dispatch(paths, "get_job", {"job_id": job.id})["status"] == "running"
    assert _dispatch(paths, "list_jobs", {})[0]["approval_status"] == "legacy"
    assert paths.audit.read_bytes() == before
    with JobStore(paths.db, paths.audit, read_only=True) as store:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            store._db.execute("DELETE FROM jobs")


@pytest.mark.parametrize("values", [("relative.db", "/audit", "/policy"), ("same", "same", "other")])
def test_server_requires_explicit_absolute_paths(values):
    with pytest.raises(ValueError):
        ServerPaths.checked(*values)


def test_server_rejects_path_aliases(tmp_path):
    path = str(tmp_path / "state")
    with pytest.raises(ValueError, match="distinct"):
        ServerPaths.checked(path, path, str(tmp_path / "policy"))
    with pytest.raises(ValueError, match="distinct"):
        ServerPaths.checked(path + ".lock", path, str(tmp_path / "policy"))
