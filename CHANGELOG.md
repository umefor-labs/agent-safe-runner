# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.3.0] - 2026-09-02

### Added

- Pending-by-default approval, operator label, decision time/reason, and proposal source.
- `inbox`, read-only `assess`, explicit `approve` / `deny`, and `list --approval`.
- Transactional decision checks and redacted audit intents; audit append failure rolls back a decision.
- Migration checklist and regression tests for approval, concurrent decisions, and old queues.

### Changed

- **Breaking:** execution and claims require approval as well as policy allowance.
- Old queued/retry-wait jobs and expired running jobs require review after migration.
- Manual retry (including dead-letter jobs) clears approval; automatic retries retain it.
- New jobs store an absolute working directory even when `--cwd` is omitted.
- Argument prefixes are case-sensitive; review policies that relied on case folding.

### Fixed

- Missing executable names no longer match each other; POSIX executable paths retain case sensitivity.
- Execution uses the resolved executable path instead of searching again in the job directory.
- Process-start failures release the lease and follow the bounded retry policy.
- Workers recover expired leases even when no other queued job exists.
- Cancellation and manual retry use conditional state updates to avoid overwriting a concurrent claim.

## [0.2.1] - 2026-08-28

### Added

- End-user installation, upgrade, uninstall, troubleshooting, and first-run instructions.
- `agent-safe --version` and `python -m agent_safe_runner` entry points.
- A harmless `python --version` rule in the generated sample policy.

### Changed

- Package metadata now links to the Umefor Labs repository, documentation, and issue tracker.
- CI action runtimes were updated and now include an installed-command smoke test.

## [0.2.0] - 2026-08-28

### Added

- Deny-by-default command policy with prefixes, working roots, argument denials, and execution limits.
- Atomic job leases, expired-lease recovery, bounded retry with backoff, cancellation, and manual retry.
- Secret-like argument rejection, minimal child environment, output redaction, and output limits.
- Hash-chained JSONL audit verification.
- Structured CLI commands for policy initialization, queue inspection, execution, workers, cancellation, retry, and audit verification.
- Migration support for databases created by `0.1.x`.
- Cross-platform CI configuration, threat model, security policy, and contributing guide.

## [0.1.0] - 2026-08-28

### Added

- Initial SQLite queue, idempotent submission, dry run, execution, JSONL audit, and CLI.
