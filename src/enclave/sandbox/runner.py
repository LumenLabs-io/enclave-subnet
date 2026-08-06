from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from enclave.sandbox.spec import SandboxSpec

__all__ = ["ContainerOutcome", "ContainerResult", "DockerRunner", "Runner"]

_STDERR_TAIL_BYTES = 4096


class ContainerOutcome(str, Enum):
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    CRASHED = "crashed"
    START_FAILED = "start_failed"


@dataclass(frozen=True, slots=True)
class ContainerResult:
    outcome: ContainerOutcome
    exit_code: int | None
    stderr_tail: str = ""

    @property
    def clean(self) -> bool:
        return self.outcome is ContainerOutcome.COMPLETED and self.exit_code == 0


@runtime_checkable
class Runner(Protocol):
    async def run(self, spec: SandboxSpec, container_name: str) -> ContainerResult: ...


class DockerRunner:
    def __init__(self, docker_binary: str = "docker") -> None:
        self._docker = docker_binary

    async def run(self, spec: SandboxSpec, container_name: str) -> ContainerResult:
        argv = spec.docker_argv(container_name)
        argv[0] = self._docker
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return ContainerResult(
                outcome=ContainerOutcome.START_FAILED,
                exit_code=None,
                stderr_tail=str(exc),
            )

        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=spec.limits.wall_clock_seconds
            )
        except TimeoutError:
            await self._kill(container_name)
            with contextlib.suppress(Exception):
                await process.wait()
            return ContainerResult(outcome=ContainerOutcome.TIMEOUT, exit_code=None)

        tail = (stderr or b"")[-_STDERR_TAIL_BYTES:].decode(errors="replace")
        if process.returncode == 0:
            return ContainerResult(
                outcome=ContainerOutcome.COMPLETED, exit_code=0, stderr_tail=tail
            )
        return ContainerResult(
            outcome=ContainerOutcome.CRASHED,
            exit_code=process.returncode,
            stderr_tail=tail,
        )

    async def _kill(self, container_name: str) -> None:
        with contextlib.suppress(Exception):
            killer = await asyncio.create_subprocess_exec(
                self._docker,
                "kill",
                container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
