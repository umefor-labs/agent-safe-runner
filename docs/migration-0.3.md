# Upgrading to 0.3.0

This release deliberately changes execution behavior: policy allowance is no
longer enough. Each job also needs a separate approval decision. There is no
bulk auto-approval or bypass flag.

## Before installing

1. Stop **all** workers and CLI processes using the queue. Wait for running
   commands to finish. Do not mix 0.2.x workers with 0.3.x; older workers do not
   check approval.
2. Make a backup of your runtime directory while it is stopped. Keep the SQLite
   database and any `-wal` / `-shm` sidecars together with the policy, audit JSONL,
   and audit lock file. Do not copy a live SQLite database piecemeal.
3. Keep the backup outside the source repository. It may contain private command
   arguments. Record your old package version with `agent-safe --version`.
4. Install the update using the command in [Getting started](getting-started.md).

## What changes automatically

The first database-opening command adds approval metadata without deleting jobs.

| Previous execution state | Result after migration |
| --- | --- |
| `queued` / `retry_wait` | Same execution state, approval `pending` |
| `running` with expired or missing lease | Requeued with approval `pending` |
| `running` with unexpired lease | Retained as `legacy`; becomes pending when the lease expires and is reclaimed |
| `succeeded` / `failed` / `cancelled` / `dead_letter` | History retained with approval `legacy` |

Existing results, attempt counts, command vectors, and job IDs are preserved.
Migration itself is not an approval decision. Newly submitted jobs always start
pending, including jobs submitted to a migrated database.

## Review before restarting workers

```bash
agent-safe --version
agent-safe list --approval legacy
agent-safe inbox
agent-safe show JOB_ID
agent-safe assess JOB_ID
agent-safe approve JOB_ID --by local-operator --reason "Reviewed after upgrade"
agent-safe work --once --execute --worker local-1
agent-safe audit-verify
```

Check exact arguments, working directory, timeout, and retry count before
approving. Policy-denied jobs cannot be force-approved. Use `deny JOB_ID --by
local-operator --reason "Not needed"` for unwanted pending proposals.

If a historical job has no stored working directory, approval fails closed. Cancel
it and resubmit with an explicit `--cwd`. If you previously supplied a custom
idempotency key, use a **new** key for the corrected submission; reusing a key
returns the original job. A manual `retry` clears approval but does not edit the
stored command or directory.

Review argument prefixes that previously relied on case-insensitive matching.
Flags and subcommands now match case-sensitively. Policy paths match the host OS's
path case semantics; missing programs no longer authorize each other.

## Rollback

Stop upgraded workers first. Preserve the upgraded state separately if it contains
new work. Restore the complete pre-upgrade backup and reinstall your previous
package version/source revision. Do **not** point an older runner at the upgraded
database: it can execute jobs while ignoring the approval state.

Restoring the backup does not undo external command side effects, and it discards
post-backup queue history from the active database. Reconcile that work manually
before restarting any worker.
