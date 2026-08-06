from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from enclave.constants import MECHANISM_VERSION
from enclave.errors import ConfigError

__all__ = ["STALE_AFTER_SECONDS", "LockState", "read_lock", "round_in_flight", "round_lock"]

_LOCK_NAME = "round.lock"
STALE_AFTER_SECONDS = 86_400


@dataclass(frozen=True, slots=True)
class LockState:
    round_id: str
    pid: int
    mechanism_version: int
    opened_at: str
    acquired_at: float = 0.0

    def as_json(self) -> dict[str, Any]:
        return {
            "round_id": self.round_id,
            "pid": self.pid,
            "mechanism_version": self.mechanism_version,
            "opened_at": self.opened_at,
            "acquired_at": self.acquired_at,
        }

    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.acquired_at) if self.acquired_at else 0.0

    def expired(self, stale_after: float = STALE_AFTER_SECONDS) -> bool:
        return bool(self.acquired_at) and self.age_seconds() > stale_after

    @property
    def holder_alive(self) -> bool:
        if self.pid <= 0:
            return False
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True
        return True


def _lock_path(state_root: Path) -> Path:
    return state_root / _LOCK_NAME


def read_lock(state_root: Path) -> LockState | None:
    path = _lock_path(state_root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return LockState(
        round_id=str(payload.get("round_id", "")),
        pid=int(payload.get("pid", 0)),
        mechanism_version=int(payload.get("mechanism_version", 0)),
        opened_at=str(payload.get("opened_at", "")),
        acquired_at=float(payload.get("acquired_at", 0.0)),
    )


def round_in_flight(state_root: Path, stale_after: float = STALE_AFTER_SECONDS) -> bool:
    lock = read_lock(state_root)
    if lock is None:
        return False
    if lock.expired(stale_after):
        return False
    return lock.holder_alive


@contextmanager
def round_lock(state_root: Path, round_id: str, opened_at: str) -> Iterator[LockState]:
    state_root.mkdir(parents=True, exist_ok=True)
    path = _lock_path(state_root)

    existing = read_lock(state_root)
    if existing is not None and existing.holder_alive:
        raise ConfigError(
            f"round {existing.round_id} is already in flight under pid {existing.pid}; "
            "refusing to evaluate two rounds from one state directory"
        )

    state = LockState(
        round_id=round_id,
        pid=os.getpid(),
        mechanism_version=MECHANISM_VERSION,
        opened_at=opened_at,
        acquired_at=time.time(),
    )
    path.write_text(
        json.dumps(state.as_json(), sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    try:
        yield state
    finally:
        try:
            current = read_lock(state_root)
            if current is not None and current.pid == state.pid:
                path.unlink()
        except OSError:
            pass
