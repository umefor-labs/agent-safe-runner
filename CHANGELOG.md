# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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
