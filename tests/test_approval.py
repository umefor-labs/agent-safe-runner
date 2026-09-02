import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_safe_runner.core import JobStore
from agent_safe_runner.errors import ApprovalRequired, InvalidJobState, PolicyViolation
from agent_safe_runner.policy import CommandRule, Policy


@pytest.fixture
def store(tmp_path):
    policy = Policy(rules=(CommandRule(sys.executable),), allowed_roots=(tmp_path.resolve(),))
    with JobStore(tmp_path / "jobs.sqlite3", tmp_path / "audit.jsonl", policy=policy,
                  retry_base_seconds=0) as instance:
        yield instance


def submit(store, **kwargs):
    return store.submit((sys.executable, "--version"), cwd=str(store.path.parent), **kwargs)


def test_pending_job_cannot_execute_or_claim(store, monkeypatch):
    job = submit(store)
    assert job.approval_status == "pending"
    assert job.source == "cli"
    assert job.decided_by is job.decided_at is job.decision_reason is None
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not execute"))
    with pytest.raises(ApprovalRequired):
        store.run(job.id, execute=True)
    with pytest.raises(ApprovalRequired):
        store.claim(job.id, "worker", 30)
    assert store.work_once(execute=True) is None
    assert store.get(job.id).attempts == 0


def test_assess_is_read_only_and_distinguishes_policy_from_approval(store):
    job = submit(store)
    before = store.audit.path.read_bytes()
    result = store.assess(job.id)
    assert result["allowed"] is True
    assert result["execution_ready"] is False
    assert result["command"] == list(job.command)
    assert result["cwd"] == job.cwd
    assert result["timeout"] == job.timeout
    assert result["matched_rule"]["program"] == sys.executable
    assert store.audit.path.read_bytes() == before
    assert store.get(job.id) == job


def test_approval_records_decision_and_allows_worker_execution(store):
    job = submit(store, source="mcp")
    approved = store.approve(job.id, by="reviewer", reason="Read-only version check")
    assert approved.decided_by == "reviewer"
    assert approved.decided_at is not None
    assert approved.decision_reason == "Read-only version check"
    assert approved.source == "mcp"
    assert store.assess(job.id)["execution_ready"] is True
    assert store.work_once(execute=True).status == "succeeded"
    assert store.audit.verify()["valid"]


def test_worker_skips_pending_jobs_in_front_of_approved_jobs(store):
    pending = submit(store, idempotency_key="pending")
    approved = submit(store, idempotency_key="approved")
    store.approve(approved.id, by="reviewer")
    assert store.work_once(execute=True).id == approved.id
    assert store.get(pending.id).status == "queued"


def test_approval_never_overrides_policy(store):
    job = submit(store)
    store.policy = Policy.deny_all()
    with pytest.raises(PolicyViolation):
        store.approve(job.id, by="reviewer")
    assert store.get(job.id).approval_status == "pending"
    assert store.assess(job.id)["allowed"] is False


def test_deny_cancels_job_without_needing_policy_allowance(store):
    job = submit(store)
    store.policy = Policy.deny_all()
    denied = store.deny(job.id, by="reviewer", reason="Not needed")
    assert denied.approval_status == "denied"
    assert denied.status == "cancelled"
    assert store.inbox() == []
    assert store.peek_next() is None
    with pytest.raises(InvalidJobState):
        store.run(job.id, execute=True)
    with pytest.raises(InvalidJobState):
        store.approve(job.id, by="another-reviewer")


@pytest.mark.parametrize("action,by,reason", [
    ("approve", "", None), ("approve", "   ", None),
    ("approve", "a" * 201, None), ("approve", "reviewer", "a" * 2001),
    ("deny", "reviewer", ""), ("deny", "reviewer", "   "),
])
def test_invalid_decision_is_rejected_without_state_change(store, action, by, reason):
    job = submit(store)
    with pytest.raises(ValueError):
        getattr(store, action)(job.id, by=by, reason=reason)
    assert store.get(job.id) == job


def test_decision_metadata_is_redacted_in_database_and_audit(store):
    job = submit(store)
    secret = "sk-" + "abcdefghijklmnop"
    decided = store.approve(job.id, by=secret, reason=f"Checked {secret}")
    assert secret not in json.dumps(decided.to_dict())
    assert secret not in store.audit.path.read_text(encoding="utf-8")
    assert "<redacted>" in decided.decision_reason


@pytest.mark.parametrize("decision", ["approve", "deny"])
def test_audit_failure_rolls_back_decision(store, monkeypatch, decision):
    job = submit(store)

    def fail(*args, **kwargs):
        raise OSError("audit volume unavailable")

    monkeypatch.setattr(store.audit, "append", fail)
    with pytest.raises(OSError):
        getattr(store, decision)(job.id, by="reviewer", reason="Reviewed")
    assert store.get(job.id) == job


def test_concurrent_opposite_decisions_have_exactly_one_winner(store):
    job = submit(store)
    barrier = threading.Barrier(2)

    def decide(action):
        with JobStore(store.path, store.audit.path, policy=store.policy) as other:
            barrier.wait(timeout=10)
            try:
                return getattr(other, action)(job.id, by=action, reason="Reviewed").approval_status
            except InvalidJobState:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, ("approve", "deny")))
    assert results.count("conflict") == 1
    assert store.get(job.id).approval_status in {"approved", "denied"}
    events = [json.loads(line) for line in store.audit.path.read_text(encoding="utf-8").splitlines()]
    assert sum(event["event"] == "job.approval_decision" for event in events) == 1
    assert store.audit.verify()["valid"]


@pytest.mark.parametrize("terminal", ["failed", "cancelled", "dead_letter", "denied"])
def test_manual_retry_requires_fresh_approval(store, terminal):
    job = store.submit((sys.executable, "-c", "raise SystemExit(1)"), cwd=str(store.path.parent))
    if terminal == "denied":
        store.deny(job.id, by="reviewer", reason="Rejected")
    else:
        store.approve(job.id, by="reviewer", reason="First decision")
        if terminal == "cancelled":
            store.cancel(job.id)
        else:
            if terminal == "dead_letter":
                store.policy = Policy.deny_all()
            assert store.run(job.id, execute=True).status == terminal
    retried = store.retry(job.id)
    assert retried.status == "queued"
    assert retried.approval_status == "pending"
    assert retried.decided_at is retried.decided_by is retried.decision_reason is None
    assert retried.attempts == 0
    with pytest.raises(ApprovalRequired):
        store.run(job.id, execute=True)


def test_automatic_retry_preserves_approval(store):
    job = store.submit((sys.executable, "-c", "raise SystemExit(1)"),
                       cwd=str(store.path.parent), max_attempts=2)
    approved = store.approve(job.id, by="reviewer")
    first = store.run(job.id, execute=True)
    assert first.status == "retry_wait"
    assert first.approval_status == "approved"
    assert first.decided_at == approved.decided_at
    assert store.work_once(execute=True).status == "failed"


def test_expired_approved_lease_recovers_even_when_no_other_job_is_queued(store):
    now = [100.0]
    store.clock = lambda: now[0]
    job = submit(store)
    store.approve(job.id, by="reviewer")
    store.claim(job.id, "crashed-worker", 1)
    now[0] = 102.0
    result = store.work_once(execute=True)
    assert result.id == job.id
    assert result.status == "succeeded"
    assert result.approval_status == "approved"


def test_spawn_error_releases_lease(store, monkeypatch):
    job = submit(store)
    store.approve(job.id, by="reviewer")

    def fail(*args, **kwargs):
        raise PermissionError("executable access denied")

    monkeypatch.setattr(subprocess, "run", fail)
    result = store.run(job.id, execute=True)
    assert result.status == "failed"
    assert result.error_class == "spawn_error"
    assert result.lease_owner is result.lease_until is None


def test_execution_uses_resolved_program_not_job_directory_search(store, monkeypatch):
    job = submit(store)
    store.approve(job.id, by="reviewer")

    def complete(command, **kwargs):
        assert command[0] == os.path.normcase(str(Path(sys.executable).resolve()))
        assert kwargs["shell"] is False
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", complete)
    assert store.run(job.id, execute=True).status == "succeeded"


def test_submission_freezes_default_working_directory(store, monkeypatch):
    monkeypatch.chdir(store.path.parent)
    job = store.submit((sys.executable, "--version"))
    monkeypatch.chdir(store.path.parent.parent)
    assert job.cwd == str(store.path.parent.resolve())
    assert store.assess(job.id)["allowed"]


def test_inbox_and_approval_filter(store):
    pending = submit(store, idempotency_key="pending")
    approved = submit(store, idempotency_key="approved")
    denied = submit(store, idempotency_key="denied")
    cancelled = submit(store, idempotency_key="cancelled")
    store.approve(approved.id, by="reviewer")
    store.deny(denied.id, by="reviewer", reason="Not needed")
    store.cancel(cancelled.id)
    assert [job.id for job in store.inbox()] == [pending.id]
    assert [job.id for job in store.list(approval="approved")] == [approved.id]
    assert [job.id for job in store.list(("cancelled",), approval="denied")] == [denied.id]
    with pytest.raises(ValueError):
        store.list(approval="invalid")


def test_migrates_old_jobs_fail_closed_and_preserves_history(tmp_path):
    database = tmp_path / "old.sqlite3"
    statuses = ("queued", "retry_wait", "running", "succeeded", "failed", "cancelled", "dead_letter")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, command TEXT NOT NULL, "
            "status TEXT NOT NULL, created_at REAL NOT NULL, result TEXT, lease_until REAL, cwd TEXT)"
        )
        for status in statuses:
            connection.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, 12, ?, 13, ?)",
                               (status, status, json.dumps([sys.executable, "--version"]), status,
                                '{"retained": true}', str(tmp_path)))
    policy = Policy(rules=(CommandRule(sys.executable),), allowed_roots=(tmp_path.resolve(),))
    with JobStore(database, tmp_path / "audit.jsonl", policy=policy, clock=lambda: 100) as migrated:
        assert len(migrated.list()) == len(statuses)
        for status in statuses:
            job = migrated.get(status)
            assert job.result == {"retained": True}
            assert job.approval_status == ("pending" if status in statuses[:3] else "legacy")
        assert migrated.get("running").status == "queued"
        assert migrated.work_once(execute=True) is None
        migrated.approve("queued", by="reviewer")
    with JobStore(database, tmp_path / "audit.jsonl", policy=policy) as reopened:
        assert reopened.get("queued").approval_status == "approved"
        assert submit(reopened).approval_status == "pending"


def test_old_job_without_cwd_requires_resubmission(store):
    job = submit(store)
    store._db.execute("UPDATE jobs SET cwd = NULL WHERE id = ?", (job.id,))
    store._db.commit()
    assert not store.assess(job.id)["allowed"]
    with pytest.raises(PolicyViolation, match="resubmit"):
        store.approve(job.id, by="reviewer")


def test_legacy_running_job_without_lease_is_recovered_pending(store):
    job = submit(store)
    store._db.execute("UPDATE jobs SET status = 'running', approval_status = 'legacy', lease_until = NULL "
                      "WHERE id = ?", (job.id,))
    store._db.commit()
    assert store.work_once(execute=True) is None
    recovered = store.get(job.id)
    assert recovered.status == "queued"
    assert recovered.approval_status == "pending"


def test_concurrent_claims_cannot_execute_same_approved_job(store):
    job = submit(store)
    store.approve(job.id, by="reviewer")
    barrier = threading.Barrier(2)

    def claim(worker):
        with JobStore(store.path, store.audit.path, policy=store.policy) as other:
            barrier.wait(timeout=10)
            try:
                return other.claim(job.id, worker, 30).status
            except InvalidJobState:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("worker-a", "worker-b")))
    assert sorted(results) == ["conflict", "running"]
    assert store.get(job.id).attempts == 1


def test_missing_executables_do_not_match_each_other():
    assert not CommandRule("missing-allowlisted-71ce").matches(("missing-command-27aa",))


def test_argument_prefix_is_case_sensitive():
    rule = CommandRule(sys.executable, ("-m", "pytest"))
    assert rule.matches((sys.executable, "-m", "pytest"))
    assert not rule.matches((sys.executable, "-M", "pytest"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX paths are case-sensitive")
def test_executable_path_matching_is_case_sensitive(tmp_path):
    upper, lower = tmp_path / "Tool", tmp_path / "tool"
    upper.write_text("first")
    lower.write_text("second")
    assert not CommandRule(str(upper)).matches((str(lower),))


@pytest.mark.parametrize("payload", [[], None, {"version": 1, "allowed_commands": [None]}])
def test_invalid_policy_shape_is_a_policy_error(tmp_path, payload):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(PolicyViolation):
        Policy.from_file(path)
