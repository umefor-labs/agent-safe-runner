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
  -> persist queued job with idempotency key
  -> append audit event

run --execute
  -> load policy
  -> authorize command, cwd, and timeout
  -> atomically lease job and increment attempt
  -> execute without a shell and with a filtered environment
  -> redact and bound captured output
  -> succeed, schedule bounded retry, or fail
  -> release lease and append audit event
```

## Concurrency

SQLite uses WAL mode and `BEGIN IMMEDIATE` while claiming a job. A claim changes the state and lease atomically. Another claim also requeues expired leases before selecting work.

The lease duration is at least the command timeout plus five seconds. Result updates verify the worker still owns the lease.

## Idempotency

If the caller does not supply a key, the runner hashes the argument vector, resolved working directory, and timeout. Repeating the same submission returns the existing job. Callers may supply a domain key when those fields do not fully describe duplicate work.

## Database compatibility

Startup checks the columns in the `jobs` table and adds fields introduced after `0.1.x`. Runtime databases are local state and are not part of the source distribution.
