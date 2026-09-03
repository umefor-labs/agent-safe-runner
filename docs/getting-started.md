# Getting started

This guide takes a new user from installation to one reviewed, policy-allowed command.
No clone of the source repository is required.

## Requirements

- Python 3.11 or newer
- Windows, macOS, or Linux

Check your Python version:

```bash
python --version
```

## Install

### Recommended: isolated installation with pipx

If `pipx` is already installed, it keeps the application separate from your
other Python packages:

```bash
pipx install agent-safe-runner
```

This installs the published [PyPI release](https://pypi.org/project/agent-safe-runner/).
If you do not have pipx, follow its [installation guide](https://pipx.pypa.io/latest/how-to/install-pipx.html).
After installation, run `pipx ensurepath` if needed and reopen your terminal.
For optional AI-agent tool integration, see [MCP setup](mcp.md).

Verify the installation:

```bash
agent-safe --version
```

### Alternative: pip in a virtual environment

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it with `.venv\Scripts\Activate.ps1` in PowerShell, or
`source .venv/bin/activate` on macOS/Linux. Then install:

```bash
python -m pip install agent-safe-runner
```

For this pip installation, the module command is also available:

```bash
python -m agent_safe_runner --version
```

The system Python cannot import an app isolated by pipx; use `agent-safe` for
pipx installations instead of the module command.

### Unreleased source from GitHub

For testing development changes in a separate environment, use:

```bash
python -m pip install "https://github.com/umefor-labs/agent-safe-runner/archive/refs/heads/main.zip"
```

The `main` branch can include changes newer than the published release.

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
agent-safe assess JOB_ID
```

The job remains queued with `approval_status: pending`. In `assess`, `allowed`
means the policy permits the command, not that it has been approved. Review the
command, directory, timeout, and `max_attempts` shown by `show`, then approve:

```bash
agent-safe approve JOB_ID --by local-operator --reason "Reviewed version check"
agent-safe run JOB_ID --execute --worker local-1
```

The result should contain `"status": "succeeded"`, a return code of `0`, and
the Python version in `stdout`.

Verify the audit chain:

```bash
agent-safe audit-verify
```

## Review the queue

```bash
agent-safe inbox
agent-safe list --approval pending
agent-safe deny JOB_ID --by local-operator --reason "Outside the requested task"
```

Denial cancels a pending job; it does not remove history. Approved queued jobs
can be cancelled with `cancel JOB_ID`. The `--by` value is only a recorded
operator label, not a login or proof of identity. Keep approval access separate
from an agent's proposal tools.

`work --once --execute` processes at most one approved, available job and reports
`idle` when none is available. `retry JOB_ID` accepts failed, cancelled, and
dead-letter jobs, resets attempts, and requires fresh approval. Automatic retries
within the reviewed `max_attempts` retain approval.

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

Before upgrading from 0.1.x/0.2.x, stop workers and back up your state. Read the
[migration checklist](migration-0.3.md); migration does not approve old jobs.

```bash
pipx upgrade agent-safe-runner
pipx uninstall agent-safe-runner
```

For a pip installation, use `python -m pip install --upgrade agent-safe-runner`
or `python -m pip uninstall agent-safe-runner` inside its activated environment.
If switching from a GitHub-source pipx installation to PyPI, uninstall the app
and install it again by package name; keep your data files in place.

Uninstalling the package does not remove local database, policy, or audit files.

## Troubleshooting

### `agent-safe` is not found

For pipx, run `pipx ensurepath`, reopen the terminal, and check `pipx list`.
For pip, activate the environment where you installed the package; you can also
run `python -m agent_safe_runner` there instead of `agent-safe`.

### A command is denied

Check that the executable and argument prefix match an `allowed_commands` rule,
the job's working directory is under an `allowed_working_roots` entry, and the
timeout is within `max_timeout_seconds`.

### `approval_required`

Run `assess JOB_ID` and `show JOB_ID`, review the proposal, then record an explicit
`approve JOB_ID --by YOUR_LABEL` decision. A policy allowance alone is not enough.
An approval that conflicts with policy is rejected; there is no force-approve.

### `invalid_job_state` while approving or denying

Read the job again with `show JOB_ID`. Another operator may already have decided,
cancelled, or claimed it. Decisions apply only to pending queued/retry-wait jobs.

### A job is in `dead_letter`

A policy denial during an execution attempt is not retried automatically. Review
the job and policy. Submit a corrected job if its command must change; otherwise
`retry JOB_ID` returns the original job to pending approval. Do not widen the
policy merely to bypass an unexpected denial.

### Important security boundary

`agent-safe-runner` is an execution gate, not a process sandbox. Use a
low-privilege account and operating-system or container isolation for untrusted
code.
