# Security policy

## Supported versions

Security fixes currently target the latest released version while the project is in alpha.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting or a private security advisory instead of opening a public issue. Include:

- affected version and operating system;
- the smallest safe reproduction;
- expected and observed behavior;
- whether secrets, command execution, policy bypass, or audit integrity are affected.

Do not include active credentials or personal data. Rotate any credential exposed during testing before sending a report.

## Security boundary

The runner does not sandbox a process. Its controls reduce accidental execution and retain evidence, but operating-system permissions remain the final boundary.
