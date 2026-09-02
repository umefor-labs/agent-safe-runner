# Threat model

## Assets

- host files and user account permissions;
- command policy and queue integrity;
- credentials present on the host;
- job results and audit evidence.

## Intended protections

- Normal runner APIs require a separate approval decision, current policy allowance, and `--execute` before spawning a process.
- Two workers should not claim the same available job through normal runner APIs.
- Common secret arguments are rejected before persistence.
- Child processes receive only allowlisted environment variables.
- Stored output is bounded and common token patterns are redacted; peak capture memory is not bounded by that setting.
- Accidental audit edits can be detected by verifying the hash chain.
- Retry loops are bounded and policy denials are not retried.

## Out of scope

- Malicious code already allowed by policy.
- Attackers with permission to change source code, policy, database, and audit files.
- Authentication of `--by` labels, or enforcement against an agent with direct CLI/database/terminal access.
- Kernel, filesystem, container, or network isolation.
- Complete secret detection in arbitrary text.
- Distributed consensus across hosts.
- Durable termination of an already-running process tree.
- Exactly-once external side effects after worker crashes or lease expiry.

## Deployment guidance

- Use a dedicated low-privilege account for workers.
- Keep policy and runtime files outside public repositories and broadly shared folders.
- Prefer narrow argument prefixes rather than allowing an entire interpreter or shell.
- Avoid allowlisting `sh`, `bash`, `cmd`, PowerShell, or unrestricted language interpreters.
- Use containers or operating-system isolation for code that is not fully trusted.
- Review queued arguments, working directory, timeout, and retry count before approving.
- Keep approval and execution capabilities out of the agent's proposal-only interface. This package does not provision OS permission separation for you.
- Treat project tests and interpreter commands as execution of project code, even when a rule uses a narrow argument prefix.
