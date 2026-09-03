# Local MCP adapter (0.4.0)

The optional adapter exposes a fixed local queue over **stdio only**. It has no
HTTP listener, approval endpoint, execution tool, telemetry exporter, or cloud
account. The ordinary CLI remains dependency-free.

## Install

Until a release is available on [PyPI](https://pypi.org/project/agent-safe-runner/),
install the MCP extra from GitHub:

```bash
pipx install "agent-safe-runner[mcp] @ https://github.com/umefor-labs/agent-safe-runner/archive/refs/heads/main.zip"
```

Once published, the equivalent PyPI command is:

```bash
pipx install "agent-safe-runner[mcp]"
```

For an existing core-only pipx installation, add the optional dependencies with
`pipx inject agent-safe-runner "mcp>=2.1,<3" "jsonschema>=4.20,<5"` after upgrading
the core to 0.4.0. With pip in a virtual environment, install the same extra using
`python -m pip install "agent-safe-runner[mcp]"` once it is published.

## Prepare a dedicated workspace

Create a policy using `agent-safe init-policy` inside that workspace. Review the
allowed commands and roots. Stop older workers and back up existing queues before
the server's startup migration; see [the migration guide](migration-0.3.md).

Use absolute paths for **all three** files. They are fixed at server startup and
cannot be redirected by a tool call:

```bash
agent-safe --db /work/agent-safe/jobs.sqlite3 --audit /work/agent-safe/audit.jsonl --policy /work/agent-safe/agent-safe-policy.json mcp
```

For Windows, substitute paths such as `C:/work/agent-safe/jobs.sqlite3`. Relative
paths are rejected, avoiding surprises from an MCP client's current directory.
Global options go before `mcp`. A missing policy denies approval/assessment,
but proposals can still be recorded for review.

When launched manually, the process waits for MCP messages on stdin; that is not
a hang. Normally your MCP host launches it and manages its lifetime. Stdout is
reserved for the protocol; diagnostics use stderr. There is no `--execute` or
network transport option on this subcommand.

## Generic host configuration

For hosts that accept an `mcpServers` configuration object:

```json
{
  "mcpServers": {
    "agent-safe": {
      "command": "agent-safe",
      "args": [
        "--db", "/work/agent-safe/jobs.sqlite3",
        "--audit", "/work/agent-safe/audit.jsonl",
        "--policy", "/work/agent-safe/agent-safe-policy.json",
        "mcp"
      ]
    }
  }
}
```

Use the actual absolute executable path if the host cannot find pipx's application
directory. Hosts differ in where they store configuration; this is a generic
example, not a claim that every desktop client has been tested.

## Available tools

| Tool | Purpose | Changes job state? |
| --- | --- | --- |
| `submit_command` | Propose an argument vector, absolute cwd, timeout, and retry limit | Creates a pending job; never approves or runs |
| `get_job` | Read a job and its captured result | No |
| `list_jobs` | Read oldest matching jobs, optionally filtered by execution/approval status | No |
| `assess_job` | Check current policy and report the matching rule | No |
| `verify_audit` | Check the configured audit hash chain | No; may create its advisory lock file |

Schemas reject unknown fields and invalid types. Submission limits are 256
arguments, 4096 characters per argument, and 65536 total command characters.
List results default to 20 jobs and are capped at 100; pagination is not provided.
Tool failures return JSON text with a stable error code and the MCP error flag.
Validation errors do not echo rejected input.

Example proposal arguments:

```json
{
  "command": ["python", "--version"],
  "cwd": "/work/agent-safe",
  "timeout": 30,
  "max_attempts": 1,
  "idempotency_key": "first-version-check"
}
```

New jobs have `source: mcp` and pending approval. MCP idempotency keys are
namespaced separately from ordinary CLI submissions. Resubmitting a key returns
the existing job unchanged, including an existing approval or completed result.
It never resets or re-executes that job. Use a new key for a genuinely new request.

## Operator review stays separate

The operator uses the normal CLI against the **same three paths**:

```bash
agent-safe --db /work/agent-safe/jobs.sqlite3 --audit /work/agent-safe/audit.jsonl --policy /work/agent-safe/agent-safe-policy.json inbox
agent-safe --db /work/agent-safe/jobs.sqlite3 --audit /work/agent-safe/audit.jsonl --policy /work/agent-safe/agent-safe-policy.json assess JOB_ID
agent-safe --db /work/agent-safe/jobs.sqlite3 --audit /work/agent-safe/audit.jsonl --policy /work/agent-safe/agent-safe-policy.json approve JOB_ID --by local-operator
agent-safe --db /work/agent-safe/jobs.sqlite3 --audit /work/agent-safe/audit.jsonl --policy /work/agent-safe/agent-safe-policy.json run JOB_ID --execute
```

Policy is reloaded on each MCP request. Read tools open SQLite in read-only mode;
schema migration happens during operator-controlled server startup, not during
read requests. There are deliberately no approve, deny, retry, cancel, or execute
MCP tools. Keep unrelated terminal/file-writing tools out of an agent's tool set
if you expect this separation to be meaningful.

## Limits and verification

Tests exercise a real subprocess/stdio connection using the official MCP Python
SDK: discovery, validation, proposal, readback, external operator approval, result
readback, policy reload, and audit tampering. This verifies protocol behavior,
not how reliably an arbitrary model chooses tools or every host's configuration.

The adapter uses MCP Python SDK 2.x (`>=2.1,<3`). The SDK includes optional
observability APIs; this project does not configure telemetry exporters or make
network calls. Approval labels are not authentication, file permissions remain
the boundary, and captured command output must be treated as untrusted data.
