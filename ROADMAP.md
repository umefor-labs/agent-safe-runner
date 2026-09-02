# Roadmap

Updated 2026-09-02. Dates are targets, not evidence of completion. This project
prioritizes useful, verifiable safeguards over feature count or artificial stars.

| Version | Focus | Acceptance gate | Target |
| --- | --- | --- | --- |
| 0.3.0 | Separate approval and execution | Pending jobs cannot execute; migration fails closed; decision races and CLI workflow tested | September 2 |
| 0.4.0 | Optional local MCP stdio adapter | Proposal/read-only tools only; real client integration tests; core works without MCP installed | September 3–6 |
| 0.4.1 | Feedback-driven beta fixes | Reproduce user reports, test clean installation, preserve compatibility | September 7–9 |
| Release freeze | Documentation and reliability | No new features; CI green; known limits and reproducible examples reviewed | September 10 |

## Next: optional MCP integration

- Candidate tools: `submit_command`, `get_job`, `list_jobs`, `assess_job`, `verify_audit`.
- No MCP approval, denial, retry, or execution tools. Operators retain those steps
  outside the agent-facing interface.
- Local stdio only, with explicit queue/policy paths and an optional dependency.
- No network service, telemetry, cloud account, or broad permission defaults.
- Test the adapter with an actual client before claiming compatibility.

## Distribution and adoption

- GitHub source-archive installation remains supported.
- PyPI publication requires a maintainer-owned account and configured trusted
  publishing; do not advertise PyPI installation before it is verified.
- Ask real users to try a small workflow and report friction. Publish examples,
  fixes, and limitations; do not buy stars, exchange stars, or spam communities.
- Keep an evidence-based release record: tests, CI, install checks, and user reports.

## Beyond this cycle

Process-tree control, streaming output limits, richer argument policies, and
stronger separation of proposal/approval permissions need their own threat-model
and design review. The current runner is not an OS sandbox or an authentication
service; these properties must not be implied by its name or documentation.
