# Contributing

Thanks for helping improve `agent-safe-runner`.

## Development setup

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m pytest
```

Keep runtime dependencies in the Python standard library unless a new dependency has a clear security and maintenance benefit.

## Pull requests

1. Open an issue before a large behavior or schema change.
2. Add focused tests for changed behavior.
3. Preserve deny-by-default execution and backward-compatible database migration.
4. Do not add examples containing real credentials, tokens, cookies, or private paths.
5. Update the README and changelog when user-facing behavior changes.

Security-sensitive changes should include the threat being addressed, assumptions, failure mode, and test evidence.

## Commit style

Use a short imperative subject, for example `Reject secrets before persistence`. Keep unrelated changes in separate commits.
