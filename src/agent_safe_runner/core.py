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
from .errors import ApprovalRequired, InvalidJobState, JobNotFound, PolicyViolation
from .policy import Policy, _resolved_program
from .redaction import redact_text, reject_sensitive_command


RUNNABLE_STATUSES = {"queued", "retry_wait"}
APPROVAL_STATUSES = ("pending", "approved", "denied", "legacy")


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
    approval_status: str = "pending"
    decided_by: str | None = None
    decided_at: float | None = None
    decision_reason: str | None = None
    source: str = "cli"

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
        read_only: bool = False,
    ):
        self.path = Path(path)
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.audit = AuditLog(audit_path)
        self.policy = policy or Policy.deny_all()
        self.clock = clock
        self.retry_base_seconds = retry_base_seconds
        self._db = (sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
                    if read_only else sqlite3.connect(self.path, timeout=10))
        self._db.row_factory = sqlite3.Row
        if not read_only:
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        if not read_only:
            self._initialize_schema()

    def _initialize_schema(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")
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
                error_class TEXT,
                approval_status TEXT NOT NULL DEFAULT 'pending',
                decided_by TEXT,
                decided_at REAL,
                decision_reason TEXT,
                source TEXT NOT NULL DEFAULT 'cli'
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
            "approval_status": "TEXT NOT NULL DEFAULT 'legacy'",
            "decided_by": "TEXT",
            "decided_at": "REAL",
            "decision_reason": "TEXT",
            "source": "TEXT NOT NULL DEFAULT 'cli'",
        }
        for column, definition in additions.items():
            if column not in existing:
                self._db.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
        self._db.execute("UPDATE jobs SET updated_at = created_at WHERE updated_at = 0")
        self._db.execute("UPDATE jobs SET available_at = created_at WHERE available_at = 0")
        self._db.execute(
            "UPDATE jobs SET approval_status = 'pending' "
            "WHERE approval_status = 'legacy' AND status IN ('queued', 'retry_wait')"
        )
        self._reclaim_expired(self.clock(), legacy_only=True)
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
            approval_status=row["approval_status"],
            decided_by=row["decided_by"],
            decided_at=row["decided_at"],
            decision_reason=row["decision_reason"],
            source=row["source"],
        )

    def submit(
        self,
        command: tuple[str, ...],
        idempotency_key: str | None = None,
        *,
        cwd: str | None = None,
        timeout: int = 60,
        max_attempts: int = 1,
        source: str = "cli",
    ) -> Job:
        if not command:
            raise ValueError("command must not be empty")
        reject_sensitive_command(command)
        if not 1 <= timeout <= 86400:
            raise ValueError("timeout must be between 1 and 86400 seconds")
        if not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20")
        if not isinstance(source, str) or not source.strip() or len(source) > 100:
            raise ValueError("source must be a nonempty label of at most 100 characters")
        source = redact_text(source.strip())
        resolved_cwd = str(Path(cwd or ".").resolve(strict=False))
        key = idempotency_key or self.key(command, resolved_cwd, timeout)
        row = self._db.execute("SELECT * FROM jobs WHERE idempotency_key = ?", (key,)).fetchone()
        if row:
            return self._job(row)
        now = self.clock()
        job_id = str(uuid.uuid4())
        self._db.execute(
            "INSERT INTO jobs (id, idempotency_key, command, status, created_at, updated_at, available_at, "
            "attempts, max_attempts, timeout, cwd, approval_status, source) "
            "VALUES (?, ?, ?, 'queued', ?, ?, ?, 0, ?, ?, ?, 'pending', ?)",
            (job_id, key, json.dumps(command), now, now, now, max_attempts, timeout, resolved_cwd, source),
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
            approval_status="pending",
            source=source,
        )
        return self.get(job_id)

    def get(self, job_id: str) -> Job:
        row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise JobNotFound(f"job not found: {job_id}")
        return self._job(row)

    def list(self, statuses: tuple[str, ...] = (), limit: int = 100, *, approval: str | None = None) -> list[Job]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if approval is not None and approval not in APPROVAL_STATUSES:
            raise ValueError("unknown approval status")
        filters, parameters = [], []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            filters.append(f"status IN ({placeholders})")
            parameters.extend(statuses)
        if approval is not None:
            filters.append("approval_status = ?")
            parameters.append(approval)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        rows = self._db.execute(
            f"SELECT * FROM jobs{where} ORDER BY created_at, id LIMIT ?", (*parameters, limit)
        ).fetchall()
        return [self._job(row) for row in rows]

    def inbox(self, limit: int = 100) -> list[Job]:
        return self.list(("queued", "retry_wait"), limit, approval="pending")

    def assess(self, job_id: str) -> dict[str, Any]:
        """Read-only policy assessment; allowed does not mean approved or executable."""
        job = self.get(job_id)
        matched = None
        try:
            if job.cwd is None:
                raise PolicyViolation("legacy job has no fixed working directory; cancel and resubmit with --cwd")
            matched = self.policy.authorize(job.command, job.cwd, job.timeout)
            allowed, reason = True, "command, working directory, and timeout match policy"
        except PolicyViolation as exc:
            allowed, reason = False, str(exc)
        return {
            "job_id": job.id, "allowed": allowed, "reason": reason,
            "command": list(job.command), "cwd": job.cwd, "timeout": job.timeout,
            "matched_rule": asdict(matched) if matched else None,
            "approval_status": job.approval_status,
            "execution_ready": allowed and job.approval_status == "approved"
            and job.status in RUNNABLE_STATUSES and job.available_at <= self.clock(),
        }

    def _decide(self, job_id: str, *, approved: bool, by: str, reason: str | None) -> Job:
        if not isinstance(by, str) or not by.strip() or len(by) > 200:
            raise ValueError("by must be a nonempty actor label of at most 200 characters")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 2000):
            raise ValueError("reason must be text of at most 2000 characters")
        if not approved and (reason is None or not reason.strip()):
            raise ValueError("denial requires a nonempty reason")
        actor = redact_text(by.strip())
        reason = redact_text(reason.strip()) if reason is not None else None
        decision = "approved" if approved else "denied"
        try:
            self._db.execute("BEGIN IMMEDIATE")
            job = self.get(job_id)
            if job.approval_status != "pending" or job.status not in RUNNABLE_STATUSES:
                raise InvalidJobState("only pending, queued or retry-wait jobs can receive a decision")
            if approved:
                assessment = self.assess(job_id)
                if not assessment["allowed"]:
                    raise PolicyViolation(assessment["reason"])
            now = self.clock()
            self._db.execute(
                "UPDATE jobs SET approval_status = ?, decided_by = ?, decided_at = ?, decision_reason = ?, "
                "updated_at = ?, status = ? WHERE id = ?",
                (decision, actor, now, reason, now, job.status if approved else "cancelled", job_id),
            )
            # Persist evidence before making approval visible to another worker.
            # SQLite and JSONL are not one transaction; this event records intent.
            self.audit.append(
                "job.approval_decision", job_id=job_id, decision=decision, decided_by=actor,
                decided_at=now, decision_reason=reason, phase="before_commit",
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return self.get(job_id)

    def approve(self, job_id: str, *, by: str, reason: str | None = None) -> Job:
        return self._decide(job_id, approved=True, by=by, reason=reason)

    def deny(self, job_id: str, *, by: str, reason: str) -> Job:
        return self._decide(job_id, approved=False, by=by, reason=reason)

    def peek_next(self) -> Job | None:
        row = self._db.execute(
            "SELECT * FROM jobs WHERE status IN ('queued', 'retry_wait') AND approval_status = 'approved' "
            "AND available_at <= ? "
            "ORDER BY available_at, created_at LIMIT 1",
            (self.clock(),),
        ).fetchone()
        return self._job(row) if row else None

    def _reclaim_expired(self, now: float, *, legacy_only: bool = False) -> list[str]:
        legacy_filter = " AND approval_status = 'legacy'" if legacy_only else ""
        rows = self._db.execute(
            "SELECT id FROM jobs WHERE status = 'running' AND (lease_until IS NULL OR lease_until < ?)"
            + legacy_filter,
            (now,),
        ).fetchall()
        identifiers = [row["id"] for row in rows]
        if identifiers:
            self._db.execute(
                "UPDATE jobs SET status = 'queued', lease_owner = NULL, lease_until = NULL, updated_at = ?, "
                "available_at = ?, approval_status = CASE WHEN approval_status = 'legacy' "
                "THEN 'pending' ELSE approval_status END WHERE status = 'running' "
                "AND (lease_until IS NULL OR lease_until < ?)"
                + legacy_filter,
                (now, now, now),
            )
        return identifiers

    def claim(self, job_id: str, worker_id: str, lease_seconds: int) -> Job:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
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
            if job.approval_status != "approved":
                raise ApprovalRequired("job requires explicit approval before execution")
            self.policy.authorize(job.command, job.cwd, job.timeout)
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
        if execute and job.approval_status != "approved":
            raise ApprovalRequired("review with assess, then approve this job before execution")
        assessment = self.assess(job_id)
        policy_allowed = assessment["allowed"]
        policy_error = None if policy_allowed else assessment["reason"]
        if execute and not policy_allowed:
            return self._dead_letter_policy_failure(job, PolicyViolation(policy_error))
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
            program = _resolved_program(claimed.command[0], claimed.cwd)
            if program is None:
                raise FileNotFoundError("approved executable is no longer available")
            completed = subprocess.run(
                (program, *claimed.command[1:]),
                cwd=claimed.cwd,
                env=self.policy.environment(),
                capture_output=True,
                text=True,
                errors="replace",
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
        except OSError as exc:
            result = {"error": redact_text(str(exc))}
            succeeded, error_class = False, "spawn_error"
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
        if execute:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                reclaimed = self._reclaim_expired(self.clock())
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
            for job_id in reclaimed:
                self.audit.append("job.lease_expired", job_id=job_id)
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
        cursor = self._db.execute(
            "UPDATE jobs SET status = 'cancelled', updated_at = ?, lease_owner = NULL, lease_until = NULL "
            "WHERE id = ? AND status IN ('queued', 'retry_wait')",
            (now, job_id),
        )
        self._db.commit()
        if cursor.rowcount != 1:
            raise InvalidJobState("job state changed before cancellation")
        self.audit.append("job.cancelled", job_id=job_id)
        return self.get(job_id)

    def retry(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status not in {"failed", "cancelled", "dead_letter"}:
            raise InvalidJobState(f"job {job_id} cannot be retried from status {job.status}")
        now = self.clock()
        cursor = self._db.execute(
            "UPDATE jobs SET status = 'queued', updated_at = ?, available_at = ?, attempts = 0, result = NULL, "
            "error_class = NULL, lease_owner = NULL, lease_until = NULL, approval_status = 'pending', "
            "decided_by = NULL, decided_at = NULL, decision_reason = NULL "
            "WHERE id = ? AND status IN ('failed', 'cancelled', 'dead_letter')",
            (now, now, job_id),
        )
        self._db.commit()
        if cursor.rowcount != 1:
            raise InvalidJobState("job state changed before retry")
        self.audit.append("job.retried", job_id=job_id)
        return self.get(job_id)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
