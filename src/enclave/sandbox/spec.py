from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Final

from enclave.errors import IsolationError

__all__ = ["DIGEST_PATTERN", "Limits", "SandboxSpec"]

DIGEST_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")

_SCRATCH_MOUNT: Final = "/scratch"
_SOCKET_DIR: Final = "/enclave"


@dataclass(frozen=True, slots=True)
class Limits:
    cpus: Decimal = Decimal("2.0")
    memory_bytes: int = 4 * 1024**3
    pids: int = 512
    wall_clock_seconds: int = 900
    scratch_bytes: int = 512 * 1024**2

    def __post_init__(self) -> None:
        if self.cpus <= 0:
            raise IsolationError("cpus must be positive")
        if self.memory_bytes <= 0:
            raise IsolationError("memory_bytes must be positive")
        if self.pids <= 0:
            raise IsolationError("pids must be positive")
        if self.wall_clock_seconds <= 0:
            raise IsolationError("wall_clock_seconds must be positive")
        if self.scratch_bytes <= 0:
            raise IsolationError("scratch_bytes must be positive")


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    image: str
    socket_dir: Path
    limits: Limits = field(default_factory=Limits)
    user: str = "65534:65534"

    def __post_init__(self) -> None:
        if not DIGEST_PATTERN.match(self.image):
            raise IsolationError(
                f"image must be pinned by digest as name@sha256:<64 hex>, got {self.image!r}"
            )

    @property
    def socket_path(self) -> Path:
        return self.socket_dir / "relay.sock"

    @property
    def container_socket_path(self) -> str:
        return f"{_SOCKET_DIR}/relay.sock"

    def docker_argv(self, container_name: str) -> list[str]:
        return [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--ipc",
            "none",
            "--user",
            self.user,
            "--pids-limit",
            str(self.limits.pids),
            "--memory",
            str(self.limits.memory_bytes),
            "--memory-swap",
            str(self.limits.memory_bytes),
            "--cpus",
            str(self.limits.cpus),
            "--tmpfs",
            f"{_SCRATCH_MOUNT}:rw,noexec,nosuid,nodev,size={self.limits.scratch_bytes}",
            "--mount",
            f"type=bind,source={self.socket_dir},target={_SOCKET_DIR}",
            "--env",
            f"ENCLAVE_SOCKET={self.container_socket_path}",
            "--env",
            f"ENCLAVE_SCRATCH={_SCRATCH_MOUNT}",
            self.image,
        ]

    def isolation_report(self) -> dict[str, str]:
        return {
            "network": "none",
            "rootfs": "read-only",
            "scratch": f"tmpfs {_SCRATCH_MOUNT} noexec,nosuid,nodev",
            "capabilities": "all dropped",
            "privilege_escalation": "denied",
            "accelerators": "none mapped",
            "user": self.user,
            "egress": "structurally impossible: no interface in the namespace",
        }
