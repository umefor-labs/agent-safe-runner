# Getting started

This guide takes a new user from installation to one policy-approved command.
No clone of the source repository is required.

## Requirements

- Python 3.11 or newer
- Windows, macOS, or Linux

Check your Python version:

```bash
python --version
```

## Install

### Simple installation

Install the current version directly from GitHub:

```bash
python -m pip install "https://github.com/umefor-labs/agent-safe-runner/archive/refs/heads/main.zip"
```

### Isolated installation with pipx

If `pipx` is already installed, it keeps the application separate from your
other Python packages:

```bash
pipx install "https://github.com/umefor-labs/agent-safe-runner/archive/refs/heads/main.zip"
```

Verify either installation:

```bash
agent-safe --version
```

If the command is not found, open a new terminal after installation or use:

```bash
python -m agent_safe_runner --version
```

## Run the first safe job

Create and enter a dedicated workspace:

```bash
mkdir agent-safe-workspace
cd agent-safe-workspace
```

Create a conservative policy:

```bash
agent-safe init-policy
```

Open `agent-safe-policy.json` and review it. Relative working roots are resolved
from the directory containing that policy file.

Queue a harmless command:

```bash
agent-safe submit --cwd . --timeout 30 -- python --version
```

Copy the `id` from the returned JSON and inspect the stored job:

```bash
agent-safe show JOB_ID
```

Assess it without execution:

```bash
agent-safe run JOB_ID
```

The job remains queued after a dry run. Execute it only after review:

```bash
agent-safe run JOB_ID --execute --worker local-1
```

The result should contain `"status": "succeeded"`, a return code of `0`, and
the Python version in `stdout`.

Verify the audit chain:

```bash
agent-safe audit-verify
```

## Add a command to the policy

Each `allowed_commands` entry matches an executable and the beginning of its
argument list. For example:

```json
{"program": "git", "args_prefix": ["status"]}
```

This permits `git status` and `git status --short`, but not `git reset`. Avoid an
empty `args_prefix` unless every argument for that executable is acceptable.
Never broadly allowlist a shell or unrestricted interpreter.

## Use custom data paths

By default, data is stored in the current directory. Global path options must
come before the subcommand:

```bash
agent-safe --db ./data/jobs.sqlite3 --audit ./data/audit.jsonl --policy ./agent-safe-policy.json list
```

## Upgrade or uninstall

```bash
python -m pip install --upgrade "https://github.com/umefor-labs/agent-safe-runner/archive/refs/heads/main.zip"
python -m pip uninstall agent-safe-runner
```

Uninstalling the package does not remove local database, policy, or audit files.

## Troubleshooting

### `agent-safe` is not found

Restart the terminal, make sure your Python scripts directory is on `PATH`, or
run `python -m agent_safe_runner` instead of `agent-safe`.

### A command is denied

Check that the executable and argument prefix match an `allowed_commands` rule,
the job's working directory is under an `allowed_working_roots` entry, and the
timeout is within `max_timeout_seconds`.

### A job is in `dead_letter`

A policy denial during an execution attempt is not retried automatically. Review
the job and policy, then submit a corrected job. Do not widen the policy merely
to bypass an unexpected denial.

### Important security boundary

`agent-safe-runner` is an execution gate, not a process sandbox. Use a
low-privilege account and operating-system or container isolation for untrusted
code.
