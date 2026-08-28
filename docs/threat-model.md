# Threat model

## Assets

- host files and user account permissions;
- command policy and queue integrity;
- credentials present on the host;
- job results and audit evidence.

## Intended protections

- An agent cannot execute a queued command unless an operator supplies `--execute` and the command matches policy.
- Two workers should not claim the same available job through normal runner APIs.
- Common secret arguments are rejected before persistence.
- Child processes receive only allowlisted environment variables.
- Captured output is bounded and common token patterns are redacted.
- Accidental audit edits can be detected by verifying the hash chain.
- Retry loops are bounded and policy denials are not retried.

## Out of scope

- Malicious code already allowed by policy.
- Attackers with permission to change source code, policy, database, and audit files.
- Kernel, filesystem, container, or network isolation.
- Complete secret detection in arbitrary text.
- Distributed consensus across hosts.
- Durable termination of an already-running process tree.

## Deployment guidance

- Use a dedicated low-privilege account for workers.
- Keep policy and runtime files outside public repositories and broadly shared folders.
- Prefer narrow argument prefixes rather than allowing an entire interpreter or shell.
- Avoid allowlisting `sh`, `bash`, `cmd`, PowerShell, or unrestricted language interpreters.
- Use containers or operating-system isolation for code that is not fully trusted.
- Review queued arguments and dry-run policy assessment before enabling execution.
