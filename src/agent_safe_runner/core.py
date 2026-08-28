from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .audit import AuditLog
from .errors import InvalidJobState, JobNotFound, PolicyViolation
from .policy import Policy
from .redaction import redact_text, reject_sensitive_command


RUNNABLE_STATUSES = {"queued", "retry_wait"}


@dataclass(frozen=True)
class Job:
    id: str
    idempotency_key: str
    command: tuple[str, ...]
    status: str
    created_at: float
    updated_at: float
    available_at: float
    attempts: int
    max_attempts: int
    timeout: int
    cwd: str | None
    lease_owner: str | None = None
    lease_until: float | None = None
    result: dict[str, Any] | None = None
    error_class: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["command"] = list(self.command)
        return payload


class JobStore:
    def __init__(
        self,
        path: str | Path = "agent-safe.sqlite3",
        audit_path: str | Path = "audit.jsonl",
        *,
        policy: Policy | None = None,
        clock: Callable[[], float] = time.time,
        retry_base_seconds: float = 2.0,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.audit = AuditLog(audit_path)
        self.policy = policy or Policy.deny_all()
        self.clock = clock
        self.retry_base_seconds = retry_base_seconds
        self._db = sqlite3.connect(self.path, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                idempotency_key TEXT UNIQUE NOT NULL,
                command TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                available_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 1,
                timeout INTEGER NOT NULL DEFAULT 60,
                cwd TEXT,
                lease_owner TEXT,
                lease_until REAL,
                result TEXT,
                error_class TEXT
            )
            """
        )
        existing = {row[1] for row in self._db.execute("PRAGMA table_info(jobs)")}
        additions = {
            "updated_at": "REAL NOT NULL DEFAULT 0",
            "available_at": "REAL NOT NULL DEFAULT 0",
            "attempts": "INTEGER NOT NULL DEFAULT 0",
            "max_attempts": "INTEGER NOT NULL DEFAULT 1",
            "timeout": "INTEGER NOT NULL DEFAULT 60",
            "cwd": "TEXT",
            "lease_owner": "TEXT",
            "lease_until": "REAL",
            "error_class": "TEXT",
        }
        for column, definition in additions.items():
            if column not in existing:
                self._db.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
        self._db.execute("UPDATE jobs SET updated_at = created_at WHERE updated_at = 0")
        self._db.execute("UPDATE jobs SET available_at = created_at WHERE available_at = 0")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_runnable ON jobs(status, available_at, created_at)")
        self._db.commit()

    @staticmethod
    def key(command: tuple[str, ...], cwd: str | None, timeout: int) -> str:
        payload = json.dumps({"command": command, "cwd": cwd, "timeout": timeout}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        result = json.loads(row["result"]) if row["result"] else None
        return Job(
            id=row["id"],
            idempotency_key=row["idempotency_key"],
            command=tuple(json.loads(row["command"])),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            available_at=row["available_at"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            timeout=row["timeout"],
            cwd=row["cwd"],
            lease_owner=row["lease_owner"],
            lease_until=row["lease_until"],
            result=result,
            error_class=row["error_class"],
        )

    def submit(
        self,
        command: tuple[str, ...],
        idempotency_key: str | None = None,
        *,
        cwd: str | None = None,
        timeout: int = 60,
        max_attempts: int = 1,
    ) -> Job:
        if not command:
            raise ValueError("command must not be empty")
        reject_sensitive_command(command)
        if not 1 <= timeout <= 86400:
            raise ValueError("timeout must be between 1 and 86400 seconds")
        if not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20")
        resolved_cwd = str(Path(cwd).resolve(strict=False)) if cwd else None
        key = idempotency_key or self.key(command, resolved_cwd, timeout)
        row = self._db.execute("SELECT * FROM jobs WHERE idempotency_key = ?", (key,)).fetchone()
        if row:
            return self._job(row)
        now = self.clock()
        job_id = str(uuid.uuid4())
        self._db.execute(
            "INSERT INTO jobs (id, idempotency_key, command, status, created_at, updated_at, available_at, "
            "attempts, max_attempts, timeout, cwd) VALUES (?, ?, ?, 'queued', ?, ?, ?, 0, ?, ?, ?)",
            (job_id, key, json.dumps(command), now, now, now, max_attempts, timeout, resolved_cwd),
        )
        self._db.commit()
        self.audit.append(
            "job.submitted",
            job_id=job_id,
            idempotency_key=key,
            command=list(command),
            cwd=resolved_cwd,
            timeout=timeout,
            max_attempts=max_attempts,
        )
        return self.get(job_id)

    def get(self, job_id: str) -> Job:
        row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise JobNotFound(f"job not found: {job_id}")
        return self._job(row)

    def list(self, statuses: tuple[str, ...] = (), limit: int = 100) -> list[Job]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            rows = self._db.execute(
                f"SELECT * FROM jobs WHERE status IN ({placeholders}) ORDER BY created_at LIMIT ?",
                (*statuses, limit),
            ).fetchall()
        else:
            rows = self._db.execute("SELECT * FROM jobs ORDER BY created_at LIMIT ?", (limit,)).fetchall()
        return [self._job(row) for row in rows]

    def peek_next(self) -> Job | None:
        row = self._db.execute(
            "SELECT * FROM jobs WHERE status IN ('queued', 'retry_wait') AND available_at <= ? "
            "ORDER BY available_at, created_at LIMIT 1",
            (self.clock(),),
        ).fetchone()
        return self._job(row) if row else None

    def _reclaim_expired(self, now: float) -> list[str]:
        rows = self._db.execute(
            "SELECT id FROM jobs WHERE status = 'running' AND lease_until IS NOT NULL AND lease_until < ?",
            (now,),
        ).fetchall()
        identifiers = [row["id"] for row in rows]
        if identifiers:
            self._db.execute(
                "UPDATE jobs SET status = 'queued', lease_owner = NULL, lease_until = NULL, updated_at = ?, "
                "available_at = ? WHERE status = 'running' AND lease_until < ?",
                (now, now, now),
            )
        return identifiers

    def claim(self, job_id: str, worker_id: str, lease_seconds: int) -> Job:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        now = self.clock()
        try:
            self._db.execute("BEGIN IMMEDIATE")
            reclaimed = self._reclaim_expired(now)
            row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise JobNotFound(f"job not found: {job_id}")
            job = self._job(row)
            if job.status not in RUNNABLE_STATUSES or job.available_at > now:
                raise InvalidJobState(f"job {job_id} is not currently runnable")
            self._db.execute(
                "UPDATE jobs SET status = 'running', attempts = attempts + 1, lease_owner = ?, lease_until = ?, "
                "updated_at = ? WHERE id = ?",
                (worker_id, now + lease_seconds, now, job_id),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        for reclaimed_id in reclaimed:
            self.audit.append("job.lease_expired", job_id=reclaimed_id)
        claimed = self.get(job_id)
        self.audit.append(
            "job.claimed",
            job_id=job_id,
            worker_id=worker_id,
            attempt=claimed.attempts,
            lease_until=claimed.lease_until,
        )
        return claimed

    def _dead_letter_policy_failure(self, job: Job, exc: PolicyViolation) -> Job:
        now = self.clock()
        result = {"error": str(exc)}
        cursor = self._db.execute(
            "UPDATE jobs SET status = 'dead_letter', updated_at = ?, result = ?, error_class = 'policy_denied', "
            "lease_owner = NULL, lease_until = NULL WHERE id = ? AND status IN ('queued', 'retry_wait')",
            (now, json.dumps(result), job.id),
        )
        self._db.commit()
        if cursor.rowcount != 1:
            raise InvalidJobState(f"job {job.id} cannot be dead-lettered from status {job.status}")
        self.audit.append("job.dead_lettered", job_id=job.id, error_class="policy_denied", error=str(exc))
        return self.get(job.id)

    def run(self, job_id: str, *, execute: bool = False, worker_id: str = "direct") -> Job:
        job = self.get(job_id)
        if execute and job.status == "succeeded":
            return job
        if execute and job.status not in RUNNABLE_STATUSES:
            raise InvalidJobState(f"job {job_id} is not currently runnable")
        try:
            self.policy.authorize(job.command, job.cwd, job.timeout)
            policy_allowed, policy_error = True, None
        except PolicyViolation as exc:
            policy_allowed, policy_error = False, str(exc)
            if execute:
                return self._dead_letter_policy_failure(job, exc)
        if not execute:
            self.audit.append(
                "job.dry_run",
                job_id=job.id,
                command=list(job.command),
                policy_allowed=policy_allowed,
                policy_error=policy_error,
            )
            return job
        claimed = self.claim(job.id, worker_id, max(job.timeout + 5, 30))
        try:
            completed = subprocess.run(
                claimed.command,
                cwd=claimed.cwd,
                env=self.policy.environment(),
                capture_output=True,
                text=True,
                timeout=claimed.timeout,
                check=False,
                shell=False,
            )
            output_limit = self.policy.max_output_chars
            result = {
                "returncode": completed.returncode,
                "stdout": redact_text(completed.stdout[-output_limit:]),
                "stderr": redact_text(completed.stderr[-output_limit:]),
                "truncated": len(completed.stdout) > output_limit or len(completed.stderr) > output_limit,
            }
            succeeded = completed.returncode == 0
            error_class = None if succeeded else "nonzero_exit"
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            result = {
                "error": "timeout",
                "stdout": redact_text(stdout[-self.policy.max_output_chars :]),
                "stderr": redact_text(stderr[-self.policy.max_output_chars :]),
            }
            succeeded, error_class = False, "timeout"
        now = self.clock()
        if succeeded:
            status, available_at = "succeeded", now
        elif claimed.attempts < claimed.max_attempts:
            status = "retry_wait"
            available_at = now + self.retry_base_seconds * (2 ** (claimed.attempts - 1))
        else:
            status, available_at = "failed", now
        cursor = self._db.execute(
            "UPDATE jobs SET status = ?, updated_at = ?, available_at = ?, result = ?, error_class = ?, "
            "lease_owner = NULL, lease_until = NULL WHERE id = ? AND status = 'running' AND lease_owner = ?",
            (status, now, available_at, json.dumps(result, ensure_ascii=False), error_class, job.id, worker_id),
        )
        self._db.commit()
        if cursor.rowcount != 1:
            raise InvalidJobState("job lease was lost before the result could be stored")
        self.audit.append(
            "job.finished",
            job_id=job.id,
            worker_id=worker_id,
            status=status,
            attempt=claimed.attempts,
            error_class=error_class,
            retry_at=available_at if status == "retry_wait" else None,
        )
        return self.get(job.id)

    def work_once(self, *, execute: bool = False, worker_id: str = "worker") -> Job | None:
        job = self.peek_next()
        if not job:
            self.audit.append("worker.idle", worker_id=worker_id)
            return None
        return self.run(job.id, execute=execute, worker_id=worker_id)

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status not in RUNNABLE_STATUSES:
            raise InvalidJobState(f"job {job_id} cannot be cancelled from status {job.status}")
        now = self.clock()
        self._db.execute(
            "UPDATE jobs SET status = 'cancelled', updated_at = ?, lease_owner = NULL, lease_until = NULL WHERE id = ?",
            (now, job_id),
        )
        self._db.commit()
        self.audit.append("job.cancelled", job_id=job_id)
        return self.get(job_id)

    def retry(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status not in {"failed", "cancelled"}:
            raise InvalidJobState(f"job {job_id} cannot be retried from status {job.status}")
        now = self.clock()
        self._db.execute(
            "UPDATE jobs SET status = 'queued', updated_at = ?, available_at = ?, attempts = 0, result = NULL, "
            "error_class = NULL, lease_owner = NULL, lease_until = NULL WHERE id = ?",
            (now, now, job_id),
        )
        self._db.commit()
        self.audit.append("job.retried", job_id=job_id)
        return self.get(job_id)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
