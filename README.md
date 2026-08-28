# agent-safe-runner

[![CI](https://github.com/umefor-labs/agent-safe-runner/actions/workflows/ci.yml/badge.svg)](https://github.com/umefor-labs/agent-safe-runner/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

`agent-safe-runner` is a small, local-first queue for automation commands proposed by AI agents. It stores jobs in SQLite, requires an explicit command policy before execution, and writes a redacted JSONL audit trail.

The project is intentionally narrow: it helps a local operator control which commands may run, when they may run, and what evidence is retained afterward.

> [!WARNING]
> This project is an execution gate, not an operating-system sandbox. Run workers with a low-privilege account and use containers or OS isolation for untrusted code.

## Why it exists

Agent workflows often grow from scripts into unattended queues. At that point, a plain `subprocess.run()` leaves important questions unanswered:

- Was the command explicitly allowed?
- Could two workers run the same job?
- Did a retry happen, and why?
- Did logs accidentally store a token?
- Can the operator verify the event history?

This runner makes those controls explicit without adding a service, broker, or cloud dependency.

## Features

- Deny-by-default JSON policy with command-prefix and working-directory rules
- Dry run by default; real execution requires `--execute`
- SQLite queue with idempotency keys and schema migration from `0.1.x`
- Atomic leases, expired-lease recovery, bounded retries, and exponential backoff
- Job cancellation, manual retry, status filtering, and one-pass worker mode
- Secret-like argument rejection before persistence
- Minimal child-process environment and redacted output capture
- Append-only JSONL audit events with a verifiable SHA-256 hash chain
- Structured JSON output and errors for scripting
- Standard-library runtime with no required third-party dependencies

## Install

Python 3.11 or newer is required.

Install the latest version directly from GitHub:

```bash
python -m pip install "https://github.com/umefor-labs/agent-safe-runner/archive/refs/heads/main.zip"
```

Confirm that the command is available:

```bash
agent-safe --version
```

The package is not on PyPI yet. If your system does not expose the `agent-safe`
command after installation, use `python -m agent_safe_runner` in its place.

For an isolated installation with `pipx`, see the
[getting-started guide](docs/getting-started.md). Contributors should use the
[development setup](CONTRIBUTING.md).

## Quick start

Create a dedicated workspace so the queue, policy, and audit files stay together:

```bash
mkdir agent-safe-workspace
cd agent-safe-workspace
agent-safe init-policy
```

The generated `agent-safe-policy.json` denies everything except a few narrow,
harmless examples. Review it before adding any command.

Queue a command that prints the installed Python version:

```bash
agent-safe submit --cwd . --timeout 30 -- python --version
```

The command returns a JSON object. Copy its `id`, then inspect the job and perform
a dry run. Replace `JOB_ID` below with that value:

```bash
agent-safe show JOB_ID
agent-safe run JOB_ID
```

The dry run checks the policy but does not execute the command. Execute only after
reviewing the stored command and policy decision:

```bash
agent-safe run JOB_ID --execute --worker local-1
```

The final JSON should report `"status": "succeeded"`. Verify the audit chain's
integrity:

```bash
agent-safe audit-verify
```

See [Getting started](docs/getting-started.md) for installation isolation,
upgrades, troubleshooting, and a complete first-run walkthrough.

## Common commands

```bash
agent-safe list
agent-safe list --status queued --status retry_wait
agent-safe show JOB_ID
agent-safe cancel JOB_ID
agent-safe retry JOB_ID
agent-safe work --once --execute --worker local-1
agent-safe audit-verify
```

Global paths must appear before the subcommand:

```bash
agent-safe --db /path/to/jobs.sqlite3 --audit /path/to/audit.jsonl --policy /path/to/policy.json list
```

Everything after `--` in `submit` is stored as an argument vector and is never
passed through a shell parser. All commands emit JSON. Expected input, state, and
policy errors return exit code `2` with a stable error code.

## Upgrade and uninstall

Upgrade to the latest GitHub version:

```bash
python -m pip install --upgrade "https://github.com/umefor-labs/agent-safe-runner/archive/refs/heads/main.zip"
```

Remove the command-line application:

```bash
python -m pip uninstall agent-safe-runner
```

Uninstalling does not delete your queue, policy, or audit files.

## Policy

Execution is denied when the policy file is absent. A policy contains:

```json
{
  "version": 1,
  "allowed_commands": [
    {"program": "python", "args_prefix": ["--version"]},
    {"program": "python", "args_prefix": ["-m", "pytest"]},
    {"program": "git", "args_prefix": ["status"]}
  ],
  "allowed_working_roots": ["."],
  "denied_arguments": ["--force", "--hard"],
  "environment_allowlist": ["PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"],
  "max_timeout_seconds": 300,
  "max_output_chars": 8000
}
```

Rules compare the executable name and the beginning of its argument list. An empty `args_prefix` allows every argument for that executable and should be used cautiously.

Never place credentials in a command. The runner rejects common secret flags and token formats, but detection cannot identify every secret. Use a dedicated secret provider and grant the worker only the environment variables it needs.

## Job states

```text
queued -> running -> succeeded
                  -> retry_wait -> running
                  -> failed
queued/retry_wait -> cancelled -> queued (manual retry)
queued/retry_wait -> dead_letter (policy denial)
```

Policy-invalid jobs are not retried automatically. Nonzero exits and timeouts retry only up to `max_attempts`.

## Data files

- `agent-safe.sqlite3`: queue state, commands, and redacted results
- `audit.jsonl`: redacted event records and hash-chain metadata
- `agent-safe-policy.json`: local execution policy

These runtime files are ignored by Git. SQLite commands are stored in plain text, so do not submit secrets or place the database in a public or broadly synchronized directory.

## Current limits

- The audit chain detects accidental edits; it is not a cryptographic signature and an attacker with write access can rebuild it.
- Audit appends use advisory file locking on Windows and POSIX; filesystems that ignore advisory locks are unsupported for multi-process writers.
- Running jobs cannot currently be interrupted by `cancel`; cancellation applies to queued and retry-wait jobs.
- There is no remote API, scheduler daemon, plugin system, or secret-provider integration yet.

See [Architecture](docs/architecture.md), [Threat model](docs/threat-model.md), [Contributing](CONTRIBUTING.md), and [Security policy](SECURITY.md).

## License

MIT
