# Architecture

## Components

`agent-safe-runner` has four small layers:

1. The CLI parses commands and emits structured JSON.
2. `Policy` decides whether a command, working directory, timeout, and environment are allowed.
3. `JobStore` owns SQLite state transitions, leases, retries, and process execution.
4. `AuditLog` writes redacted JSONL events and verifies their hash chain.

The runtime has no network listener and no required third-party dependency. Audit writers use an advisory lock file so independent local worker processes do not interleave hash-chain updates.

## Execution flow

```text
submit
  -> reject secret-like arguments
  -> persist queued + pending job with idempotency key and fixed working directory
  -> append audit event

assess / inbox
  -> inspect without approving or executing

approve / deny
  -> atomically check pending, runnable state
  -> approval must also pass current policy
  -> store redacted actor, time, and reason
  -> append decision intent before committing SQLite state
  -> approved stays queued; denied becomes cancelled

run --execute
  -> load policy
  -> require approval
  -> authorize command, cwd, and timeout
  -> atomically lease job and increment attempt
  -> execute without a shell and with a filtered environment
  -> redact and bound captured output
  -> succeed, schedule bounded retry, or fail
  -> release lease and append audit event
```

## Concurrency

SQLite uses WAL mode and `BEGIN IMMEDIATE` for schema migration, approval decisions,
and claims. Concurrent approve/deny calls have only one winner. A claim rechecks
approval and policy under the write lock before incrementing the attempt.
Claims and executing workers requeue expired leases before selecting work.

The lease duration is at least the command timeout plus five seconds. Result updates verify the worker still owns the lease.

Recovery is at-least-once: an expired lease does not prove its old process stopped.
External side effects must be idempotent. Timeout handling does not guarantee
termination of a whole descendant process tree. Output is truncated for storage
after capture, not streamed under a memory cap.

## Approval and audit durability

Approval state is independent from execution status. Decisions include
`approval_status`, `decided_by`, `decided_at`, `decision_reason`, and job `source`
(default `cli`; future integrations may identify their proposals separately).
Actor and source labels are not verified identities. The database and policy must
be protected by deployment permissions.

An automatic retry keeps its original approval. Manual retry from failed,
cancelled, or dead-letter state resets approval to pending and clears decision
metadata; the previous decision remains in the audit history.

A decision is written under the SQLite lock. Its redacted
`job.approval_decision` audit event has `phase: before_commit`; if the append fails,
the database decision rolls back and no worker sees it. These are two different
storage systems: a crash or commit failure after a successful append can leave an
intent event without a committed decision. Use the database's current state to
confirm the outcome; a JSONL event alone is not authorization. Other queue/audit
operations also do not constitute a shared atomic transaction.

## Idempotency

If the caller does not supply a key, the runner hashes the argument vector, resolved working directory, and timeout. Repeating the same submission returns the existing job. Callers may supply a domain key when those fields do not fully describe duplicate work.

## Database compatibility

Startup adds missing columns. Historical records retain `legacy` approval state;
old queued/retry-wait and expired running jobs become pending. Missing historical
working directories are not inferred; those jobs need a corrected submission.
Runtime databases are local state and are not part of the source distribution.
Stop old workers and follow the [migration guide](migration-0.3.md) before upgrading.
