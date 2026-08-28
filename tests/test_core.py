import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_safe_runner.audit import AuditLog
from agent_safe_runner.core import JobStore
from agent_safe_runner.errors import AuditIntegrityError, PolicyViolation, SensitiveInputError
from agent_safe_runner.policy import CommandRule, Policy


def allow_python(root, *, max_output=8000):
    return Policy(
        rules=(CommandRule(sys.executable),),
        allowed_roots=(root.resolve(),),
        max_output_chars=max_output,
    )


def make_store(tmp_path, **kwargs):
    return JobStore(tmp_path / "jobs.sqlite3", tmp_path / "audit.jsonl", **kwargs)


def test_submit_is_idempotent(tmp_path):
    store = make_store(tmp_path)
    first = store.submit((sys.executable, "-c", "print('ok')"), cwd=str(tmp_path))
    second = store.submit((sys.executable, "-c", "print('ok')"), cwd=str(tmp_path))
    assert first.id == second.id
    assert len(store.list()) == 1


def test_submit_rejects_secret_like_arguments_before_persistence(tmp_path):
    store = make_store(tmp_path)
    fake_token = "sk-" + "abcdefghijklmnop"
    with pytest.raises(SensitiveInputError):
        store.submit(("tool", "--token", fake_token))
    assert store.list() == []


def test_submit_rejects_bare_secret_flag(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(SensitiveInputError):
        store.submit(("tool", "--token"))


def test_dry_run_records_policy_assessment_without_execution(tmp_path):
    store = make_store(tmp_path)
    job = store.submit((sys.executable, "-c", "raise SystemExit(9)"), cwd=str(tmp_path))
    result = store.run(job.id)
    assert result.status == "queued"
    last = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[-1])
    assert last["event"] == "job.dry_run"
    assert last["data"]["policy_allowed"] is False


def test_policy_denial_dead_letters_without_execution(tmp_path):
    store = make_store(tmp_path)
    job = store.submit((sys.executable, "-c", "raise SystemExit(9)"), cwd=str(tmp_path))
    result = store.run(job.id, execute=True)
    assert result.status == "dead_letter"
    assert result.attempts == 0
    assert result.error_class == "policy_denied"


def test_execute_records_redacted_output(tmp_path):
    store = make_store(tmp_path, policy=allow_python(tmp_path))
    code = "print('sk-' + 'abcdefghijklmnop')"
    job = store.submit((sys.executable, "-c", code), cwd=str(tmp_path))
    result = store.run(job.id, execute=True)
    fake_token = "sk-" + "abcdefghijklmnop"
    assert result.status == "succeeded"
    assert "<redacted>" in result.result["stdout"]
    assert fake_token not in result.result["stdout"]


def test_succeeded_job_is_not_reopened_after_policy_changes(tmp_path):
    store = make_store(tmp_path, policy=allow_python(tmp_path))
    job = store.submit((sys.executable, "-c", "print('ok')"), cwd=str(tmp_path))
    succeeded = store.run(job.id, execute=True)
    store.policy = Policy.deny_all()
    assert store.run(job.id, execute=True).status == "succeeded"
    assert store.get(job.id).attempts == succeeded.attempts


def test_failure_retries_with_bound_and_then_fails(tmp_path):
    store = make_store(tmp_path, policy=allow_python(tmp_path), retry_base_seconds=0)
    job = store.submit((sys.executable, "-c", "raise SystemExit(7)"), cwd=str(tmp_path), max_attempts=2)
    first = store.run(job.id, execute=True, worker_id="worker-a")
    second = store.run(job.id, execute=True, worker_id="worker-a")
    assert first.status == "retry_wait"
    assert second.status == "failed"
    assert second.attempts == 2
    assert second.error_class == "nonzero_exit"


def test_cancelled_job_can_be_manually_retried(tmp_path):
    store = make_store(tmp_path)
    job = store.submit(("git", "status"))
    assert store.cancel(job.id).status == "cancelled"
    retried = store.retry(job.id)
    assert retried.status == "queued"
    assert retried.attempts == 0


def test_expired_lease_is_reclaimed_when_another_job_is_claimed(tmp_path):
    now = [100.0]
    store = make_store(tmp_path, clock=lambda: now[0])
    first = store.submit(("git", "status"), idempotency_key="first")
    second = store.submit(("git", "status"), idempotency_key="second")
    store.claim(first.id, "worker-a", lease_seconds=1)
    now[0] = 102.0
    store.claim(second.id, "worker-b", lease_seconds=10)
    assert store.get(first.id).status == "queued"
    assert store.get(first.id).lease_owner is None


def test_audit_chain_detects_tampering(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.append("one", value=1)
    audit.append("two", value=2)
    assert audit.verify() == {"valid": True, "entries": 2}
    content = (tmp_path / "audit.jsonl").read_text().replace('"value": 1', '"value": 9')
    (tmp_path / "audit.jsonl").write_text(content)
    with pytest.raises(AuditIntegrityError):
        audit.verify()


def test_separate_audit_instances_serialize_writes(tmp_path):
    path = tmp_path / "audit.jsonl"

    def write(index):
        AuditLog(path).append("parallel", index=index)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(20)))
    assert AuditLog(path).verify() == {"valid": True, "entries": 20}


def test_policy_enforces_prefix_root_and_timeout(tmp_path):
    policy = Policy(
        rules=(CommandRule("git", ("status",)),),
        allowed_roots=(tmp_path.resolve(),),
        denied_arguments=("--hard",),
        max_timeout_seconds=30,
    )
    policy.authorize(("git", "status", "--short"), str(tmp_path), 10)
    with pytest.raises(PolicyViolation):
        policy.authorize(("git", "reset", "--hard"), str(tmp_path), 10)
    with pytest.raises(PolicyViolation):
        policy.authorize(("git", "status"), str(tmp_path.parent), 10)
    with pytest.raises(PolicyViolation):
        policy.authorize(("git", "status"), str(tmp_path), 31)


def test_policy_rejects_same_named_executable_from_another_path(tmp_path):
    fake = tmp_path / ("git.exe" if sys.platform == "win32" else "git")
    fake.write_text("not an executable")
    policy = Policy(rules=(CommandRule("git", ("status",)),), allowed_roots=(tmp_path.resolve(),))
    with pytest.raises(PolicyViolation):
        policy.authorize((str(fake), "status"), str(tmp_path), 10)


def test_migrates_original_mvp_database(tmp_path):
    database = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, command TEXT NOT NULL, "
        "status TEXT NOT NULL, created_at REAL NOT NULL, result TEXT)"
    )
    connection.execute(
        "INSERT INTO jobs VALUES ('old', 'key', '[\"git\", \"status\"]', 'queued', 12.0, NULL)"
    )
    connection.commit()
    connection.close()
    store = JobStore(database, tmp_path / "audit.jsonl")
    migrated = store.get("old")
    assert migrated.updated_at == 12.0
    assert migrated.available_at == 12.0
    assert migrated.max_attempts == 1
