from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .audit import AuditLog
from .core import APPROVAL_STATUSES, JobStore
from .errors import RunnerError
from .policy import Policy


STATUSES = ("queued", "running", "retry_wait", "succeeded", "failed", "cancelled", "dead_letter")


def _emit(payload: Any, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stream)


def _command(parts: Sequence[str]) -> tuple[str, ...]:
    values = list(parts)
    if values and values[0] == "--":
        values.pop(0)
    if not values:
        raise ValueError("a command is required after --")
    return tuple(values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-safe", description="Local-first, policy-gated job runner")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--db", default="agent-safe.sqlite3", help="SQLite queue path")
    parser.add_argument("--audit", default="audit.jsonl", help="JSONL audit path")
    parser.add_argument("--policy", default="agent-safe-policy.json", help="Policy JSON path")
    sub = parser.add_subparsers(dest="action", required=True)

    init_policy = sub.add_parser("init-policy", help="Write a conservative sample policy")
    init_policy.add_argument("path", nargs="?", default="agent-safe-policy.json")

    submit = sub.add_parser("submit", help="Queue a command without executing it")
    submit.add_argument("--key")
    submit.add_argument("--cwd")
    submit.add_argument("--timeout", type=int, default=60)
    submit.add_argument("--max-attempts", type=int, default=1)
    submit.add_argument("command", nargs=argparse.REMAINDER, help="Use: submit [options] -- program arg...")

    listing = sub.add_parser("list", help="List jobs")
    listing.add_argument("--status", action="append", choices=STATUSES, default=[])
    listing.add_argument("--limit", type=int, default=100)
    listing.add_argument("--approval", choices=APPROVAL_STATUSES)

    inbox = sub.add_parser("inbox", help="List pending jobs awaiting an operator decision")
    inbox.add_argument("--limit", type=int, default=100)

    assess = sub.add_parser("assess", help="Report policy allowance without approving or executing")
    assess.add_argument("job_id")

    approve = sub.add_parser("approve", help="Approve a pending job that also passes policy")
    approve.add_argument("job_id")
    approve.add_argument("--by", required=True, help="Operator label, not authentication")
    approve.add_argument("--reason")

    deny = sub.add_parser("deny", help="Deny and cancel a pending job")
    deny.add_argument("job_id")
    deny.add_argument("--by", required=True, help="Operator label, not authentication")
    deny.add_argument("--reason", required=True)

    show = sub.add_parser("show", help="Show one job")
    show.add_argument("job_id")

    run = sub.add_parser("run", help="Dry-run a job; --execute requires approval and policy allowance")
    run.add_argument("job_id")
    run.add_argument("--execute", action="store_true")
    run.add_argument("--worker", default="direct")

    work = sub.add_parser("work", help="Process at most one available job")
    work.add_argument("--once", action="store_true", required=True)
    work.add_argument("--execute", action="store_true")
    work.add_argument("--worker", default="worker")

    cancel = sub.add_parser("cancel", help="Cancel a queued job")
    cancel.add_argument("job_id")

    retry = sub.add_parser("retry", help="Reset failed, cancelled, or dead-letter jobs to pending approval")
    retry.add_argument("job_id")

    sub.add_parser("audit-verify", help="Verify the JSONL audit hash chain")
    sub.add_parser("mcp", help="Serve proposal/read-only MCP over stdio; requires absolute global paths and [mcp]")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.action == "init-policy":
            destination = Path(args.path)
            if destination.exists():
                raise ValueError(f"policy already exists: {destination}")
            destination.write_text(json.dumps(Policy.sample(), indent=2) + "\n", encoding="utf-8")
            _emit({"status": "created", "path": str(destination.resolve())})
            return 0
        if args.action == "audit-verify":
            _emit(AuditLog(args.audit).verify())
            return 0
        if args.action == "mcp":
            from .mcp_server import serve

            serve(args.db, args.audit, args.policy)
            return 0

        policy = Policy.from_file(args.policy)
        with JobStore(args.db, args.audit, policy=policy) as store:
            if args.action == "submit":
                job = store.submit(
                    _command(args.command),
                    args.key,
                    cwd=args.cwd,
                    timeout=args.timeout,
                    max_attempts=args.max_attempts,
                )
                _emit(job.to_dict())
            elif args.action == "list":
                _emit([job.to_dict() for job in store.list(tuple(args.status), args.limit, approval=args.approval)])
            elif args.action == "inbox":
                _emit([job.to_dict() for job in store.inbox(args.limit)])
            elif args.action == "assess":
                _emit(store.assess(args.job_id))
            elif args.action == "approve":
                _emit(store.approve(args.job_id, by=args.by, reason=args.reason).to_dict())
            elif args.action == "deny":
                _emit(store.deny(args.job_id, by=args.by, reason=args.reason).to_dict())
            elif args.action == "show":
                _emit(store.get(args.job_id).to_dict())
            elif args.action == "run":
                _emit(store.run(args.job_id, execute=args.execute, worker_id=args.worker).to_dict())
            elif args.action == "work":
                job = store.work_once(execute=args.execute, worker_id=args.worker)
                _emit({"status": "idle"} if job is None else job.to_dict())
            elif args.action == "cancel":
                _emit(store.cancel(args.job_id).to_dict())
            elif args.action == "retry":
                _emit(store.retry(args.job_id).to_dict())
        return 0
    except (RunnerError, ValueError, OSError) as exc:
        code = getattr(exc, "code", "invalid_input")
        _emit({"error": {"code": code, "message": str(exc)}}, error=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
