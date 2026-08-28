# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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
