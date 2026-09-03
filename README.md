# agent-safe-runner

[![CI](https://github.com/umefor-labs/agent-safe-runner/actions/workflows/ci.yml/badge.svg)](https://github.com/umefor-labs/agent-safe-runner/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

`agent-safe-runner` is a small, local-first queue for automation commands proposed by AI agents. It stores jobs in SQLite, requires a separate approval decision and an explicit command policy before execution, and writes a redacted JSONL audit trail.

The project is intentionally narrow: it helps a local operator control which commands may run, when they may run, and what evidence is retained afterward.

> [!WARNING]
> This project is an execution gate, not an operating-system sandbox. Run workers with a low-privilege account and use containers or OS isolation for untrusted code.

## Why it exists

Agent workflows often grow from scripts into unattended queues. At that point, a plain `subprocess.run()` leaves important questions unanswered:

- Was the command explicitly allowed?
- Who reviewed it before execution?
- Could two workers run the same job?
- Did a retry happen, and why?
- Did logs accidentally store a token?
- Can the operator verify the event history?

This runner makes those controls explicit without adding a service, broker, or cloud dependency.

## Features

- Deny-by-default JSON policy with command-prefix and working-directory rules
- Approval inbox, read-only assessment, and explicit approve/deny decisions
- Dry run by default; real execution requires approval, policy allowance, and `--execute`
- SQLite queue with idempotency keys and fail-closed schema migration from `0.1.x` / `0.2.x`
- Atomic leases, expired-lease recovery, bounded retries, and exponential backoff
- Job cancellation, manual retry, status filtering, and one-pass worker mode
- Secret-like argument rejection before persistence
- Minimal child-process environment and redacted output capture
- Append-only JSONL audit events with a verifiable SHA-256 hash chain
- Structured JSON output and errors for scripting
- Optional stdio MCP adapter with proposal/read-only tools; no approval or execution tools
- Standard-library runtime with no required third-party dependencies

## Install

Python 3.11 or newer is required.

Install the published release from [PyPI](https://pypi.org/project/agent-safe-runner/)
in an isolated environment:

```bash
pipx install agent-safe-runner
```

Confirm that the command is available:

```bash
agent-safe --version
```

Need pipx first? Follow the [official installation guide](https://pipx.pypa.io/latest/how-to/install-pipx.html).
If `agent-safe` is not found, run `pipx ensurepath` and reopen your terminal.
Prefer pip? In an activated virtual environment, use
`python -m pip install agent-safe-runner`.

For AI-agent integrations, see the [optional MCP adapter](docs/mcp.md).

For setup, troubleshooting, and GitHub source installation, see the
[getting-started guide](docs/getting-started.md). Contributors should use the
[development setup](CONTRIBUTING.md).

## Quick start

Create a dedicated workspace so the queue, policy, and audit files stay together:

```bash
mkdir agent-safe-workspace
cd agent-safe-workspace
agent-safe init-policy
```

The generated `agent-safe-policy.json` contains a few example rules. Review it
before use: `python --version` prints a version, but `pytest` executes project
code and is appropriate only in a trusted workspace.

Queue a command that prints the installed Python version:

```bash
agent-safe submit --cwd . --timeout 30 -- python --version
```

The command returns a JSON object. Copy its `id`, then inspect the job and perform
a dry run. Replace `JOB_ID` below with that value:

```bash
agent-safe show JOB_ID
agent-safe assess JOB_ID
agent-safe run JOB_ID
```

`assess` returns `allowed`, `reason`, and `matched_rule`. An allowed job is still
pending approval. `run` without `--execute` remains a dry run.

After reviewing the exact command, directory, timeout, and retry limit, record
your decision. Replace `local-operator` with a label meaningful to you:

```bash
agent-safe approve JOB_ID --by local-operator --reason "Reviewed version check"
agent-safe run JOB_ID --execute --worker local-1
```

`--by` is an audit label, **not authentication**. Anyone with access to the
approval CLI or writable database can approve jobs; this is a workflow gate.

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
agent-safe inbox
agent-safe list --approval pending
agent-safe list --status queued --status retry_wait
agent-safe show JOB_ID
agent-safe assess JOB_ID
agent-safe deny JOB_ID --by local-operator --reason "Not needed"
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

`work --once --execute` picks only approved jobs. Manual `retry` clears the old
decision and requires fresh approval; automatic retries keep the existing
approval for the unchanged job. `deny` cancels a pending job. To stop an already
approved queued job, use `cancel`.

## Upgrade and uninstall

**Upgrading from 0.2.x or older?** Stop all workers and back up your local state
before installing. Old queued jobs become pending and will not run until
reviewed. See the [0.3 migration guide](docs/migration-0.3.md).

Upgrade a pipx installation to the latest published release:

```bash
pipx upgrade agent-safe-runner
```

Remove the command-line application:

```bash
pipx uninstall agent-safe-runner
```

For a pip installation, use `python -m pip install --upgrade agent-safe-runner`
or `python -m pip uninstall agent-safe-runner` inside its virtual environment.
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

Rules compare resolved executable paths and the case-sensitive beginning of the argument list. Missing executables do not match. An empty `args_prefix` allows every argument for that executable and should be used cautiously.

Never place credentials in a command. The runner rejects common secret flags and token formats, but detection cannot identify every secret. Use a dedicated secret provider and grant the worker only the environment variables it needs.

## Job states

```text
queued -> running -> succeeded
                  -> retry_wait -> running
                  -> failed
queued/retry_wait -> cancelled -> queued (manual retry)
queued/retry_wait -> dead_letter (policy denial)
```

Approval is separate from execution status: `pending`, `approved`, `denied`, or
`legacy` for historical records. New jobs start `queued` + `pending`.
Policy-invalid approved jobs become `dead_letter` without spawning a process.
Nonzero exits, timeouts, and process-start failures retry up to `max_attempts`.

## Data files

- `agent-safe.sqlite3`: queue state, commands, and redacted results
- `audit.jsonl`: redacted event records and hash-chain metadata
- `agent-safe-policy.json`: local execution policy

These runtime files are ignored by Git. SQLite commands are stored in plain text, so do not submit secrets or place the database in a public or broadly synchronized directory.

## Current limits

- The audit chain detects accidental edits; it is not a cryptographic signature and an attacker with write access can rebuild it.
- Audit appends use advisory file locking on Windows and POSIX; filesystems that ignore advisory locks are unsupported for multi-process writers.
- Running jobs cannot currently be interrupted by `cancel`; cancellation applies to queued and retry-wait jobs.
- Approval records are not signatures or user authentication. This gate cannot constrain an agent that already has unrestricted terminal or file access.
- SQLite state and JSONL audit are separate stores, not a single crash-atomic transaction. See [Architecture](docs/architecture.md).
- Output limits bound stored text, not peak capture memory; lease recovery is at-least-once, not an exactly-once guarantee for external side effects.
- MCP is local stdio only. There is no remote API, scheduler daemon, plugin system, or secret-provider integration yet.

See [Architecture](docs/architecture.md), [Threat model](docs/threat-model.md), [Roadmap](ROADMAP.md), [Contributing](CONTRIBUTING.md), and [Security policy](SECURITY.md).

## License

MIT
