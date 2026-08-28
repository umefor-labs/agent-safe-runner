from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import AuditIntegrityError
from .redaction import redact


@contextmanager
def _file_lock(path: Path):
    """Use a one-byte advisory lock on Windows and flock on POSIX."""
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class AuditLog:
    """Append-only JSONL audit log with a hash chain for accidental tamper detection."""

    _registry_lock = threading.Lock()
    _path_locks: dict[str, threading.Lock] = {}

    def __init__(self, path: str | Path):
        self.path = Path(path)
        lock_key = str(self.path.resolve(strict=False)).casefold()
        with self._registry_lock:
            self._lock = self._path_locks.setdefault(lock_key, threading.Lock())

    @staticmethod
    def _digest(record: dict[str, Any]) -> str:
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _tail(self) -> tuple[int, str | None]:
        if not self.path.exists():
            return 0, None
        last = None
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last = line
        if last is None:
            return 0, None
        try:
            record = json.loads(last)
            return int(record["seq"]), str(record["hash"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AuditIntegrityError("cannot append to an invalid audit log") from exc

    def append(self, event: str, **data: object) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with _file_lock(self.path):
                sequence, previous_hash = self._tail()
                record: dict[str, Any] = {
                    "seq": sequence + 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": event,
                    "prev_hash": previous_hash,
                    "data": redact(data),
                }
                record["hash"] = self._digest(record)
                descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
                try:
                    payload = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
                    os.write(descriptor, payload)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                return record

    def verify(self) -> dict[str, int | bool]:
        expected_sequence = 1
        previous_hash = None
        entries = 0
        if not self.path.exists():
            return {"valid": True, "entries": 0}
        with self._lock:
            with _file_lock(self.path):
                with self.path.open("r", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if not line.strip():
                            continue
                        try:
                            record = json.loads(line)
                            actual_hash = record.pop("hash")
                        except (KeyError, json.JSONDecodeError) as exc:
                            raise AuditIntegrityError(f"invalid audit entry at line {line_number}") from exc
                        if record.get("seq") != expected_sequence or record.get("prev_hash") != previous_hash:
                            raise AuditIntegrityError(f"broken audit chain at line {line_number}")
                        calculated_hash = self._digest(record)
                        if actual_hash != calculated_hash:
                            raise AuditIntegrityError(f"audit hash mismatch at line {line_number}")
                        previous_hash = actual_hash
                        expected_sequence += 1
                        entries += 1
        return {"valid": True, "entries": entries}
