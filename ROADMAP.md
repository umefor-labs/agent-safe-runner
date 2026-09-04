# Roadmap

Updated 2026-09-04. Dates are targets, not evidence of completion. This project
prioritizes useful, verifiable safeguards over feature count or artificial stars.

| Version | Focus | Acceptance gate | Target |
| --- | --- | --- | --- |
| 0.3.0 | Separate approval and execution | Pending jobs cannot execute; migration fails closed; decision races and CLI workflow tested | September 2 |
| 0.4.0 | Optional local MCP stdio adapter | Proposal/read-only tools only; real client integration tests; core works without MCP installed | September 3–6 |
| 0.4.1 | Policy validation and user-facing diagnostics | Malformed policies rejected; read-only validation; clean installation and regression tests | September 4 |
| Release freeze | Documentation and reliability | No new features; CI green; known limits and reproducible examples reviewed | September 10 |

## 0.4.0: optional MCP integration

- Tools: `submit_command`, `get_job`, `list_jobs`, `assess_job`, `verify_audit`.
- No MCP approval, denial, retry, or execution tools. Operators retain those steps
  outside the agent-facing interface.
- Local stdio only, with explicit queue/policy paths and an optional dependency.
- No network service, telemetry, cloud account, or broad permission defaults.
- Subprocess stdio tests use the official SDK client; individual desktop hosts and model tool-selection behavior need separate validation.

## Distribution and adoption

0.4.1 focuses on maintainer-reproduced policy parsing defects and installation
diagnostics; no external user reports were available when this patch was prepared.
It adds `check-policy` without expanding MCP permissions or execution authority.

- GitHub source-archive installation remains supported.
- [0.4.0 is published on PyPI](https://pypi.org/project/agent-safe-runner/0.4.0/).
  A clean `pipx install agent-safe-runner` returned `agent-safe 0.4.0` on
  September 3, 2026. The [release workflow](https://github.com/umefor-labs/agent-safe-runner/actions/runs/33763459743)
  passed all eight Windows/Linux Python 3.11–3.14 test jobs, package validation,
  and the exact-wheel pipx check before publishing via short-lived OIDC credentials.
  Future releases must follow the same [publishing checklist](docs/publishing.md).
- Ask real users to try a small workflow and report friction. Publish examples,
  fixes, and limitations; do not buy stars, exchange stars, or spam communities.
- Keep an evidence-based release record: tests, CI, install checks, and user reports.

## Beyond this cycle

Process-tree control, streaming output limits, richer argument policies, and
stronger separation of proposal/approval permissions need their own threat-model
and design review. The current runner is not an OS sandbox or an authentication
service; these properties must not be implied by its name or documentation.
