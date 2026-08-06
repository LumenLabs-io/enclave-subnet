from __future__ import annotations

import json
import os
import socket
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any

from enclave.errors import ProtocolError, RelayError
from enclave.relay.protocol import SOCKET_PATH, Method

__all__ = ["Briefing", "Completion", "EnclaveClient", "Observation"]

_READ_CHUNK = 65536


@dataclass(frozen=True, slots=True)
class Briefing:
    instance_id: str
    seed: str
    environment: str
    spend_cap: str
    deadline_seconds: int
    models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Completion:
    content: str
    input_tokens: int
    output_tokens: int
    remaining: str

    @property
    def usage(self) -> tuple[int, int]:
        return self.input_tokens, self.output_tokens


@dataclass(frozen=True, slots=True)
class Observation:
    text: str
    terminal: bool
    remaining: str


class EnclaveClient:
    def __init__(self, socket_path: str | None = None, timeout: float = 120.0) -> None:
        self._path: str = socket_path or os.environ.get("ENCLAVE_SOCKET") or SOCKET_PATH
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._buffer = b""
        self._next_id = 0

    def __enter__(self) -> EnclaveClient:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def connect(self) -> None:
        if self._sock is not None:
            return
        family = getattr(socket, "AF_UNIX", None)
        if family is None:
            raise RelayError("unix domain sockets are unavailable on this platform")
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        sock.connect(self._path)
        self._sock = sock

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def _call(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        if self._sock is None:
            self.connect()
        if self._sock is None:
            raise RelayError("relay socket is not connected")

        self._next_id += 1
        frame = json.dumps(
            {"id": self._next_id, "method": method, "params": dict(params)},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        self._sock.sendall(frame + b"\n")

        while b"\n" not in self._buffer:
            chunk = self._sock.recv(_READ_CHUNK)
            if not chunk:
                raise RelayError("relay closed the connection")
            self._buffer += chunk

        line, _, self._buffer = self._buffer.partition(b"\n")
        payload = json.loads(line)
        if "error" in payload:
            error = payload["error"]
            raise RelayError(f"{error.get('code')}: {error.get('message')}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ProtocolError("relay returned a malformed result")
        return result

    def initialise(self) -> Briefing:
        result = self._call(Method.INITIALISE, {})
        return Briefing(
            instance_id=str(result.get("instance_id", "")),
            seed=str(result.get("seed", "")),
            environment=str(result.get("environment", "")),
            spend_cap=str(result.get("spend_cap", "")),
            deadline_seconds=int(result.get("deadline_seconds", 0)),
            models=tuple(str(m) for m in result.get("models", ())),
        )

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        model: str | None = None,
        max_tokens: int = 1024,
        stop: Sequence[str] = (),
    ) -> Completion:
        params: dict[str, Any] = {
            "messages": [dict(m) for m in messages],
            "max_tokens": max_tokens,
            "stop": list(stop),
        }
        if model is not None:
            params["model"] = model
        result = self._call(Method.COMPLETIONS, params)
        usage = result.get("usage", {})
        return Completion(
            content=str(result.get("content", "")),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            remaining=str(result.get("budget", {}).get("remaining", "")),
        )

    def act(self, action: str, **arguments: Any) -> Observation:
        result = self._call(Method.ACT, {"action": action, "arguments": arguments})
        return Observation(
            text=str(result.get("observation", "")),
            terminal=bool(result.get("terminal", False)),
            remaining=str(result.get("budget", {}).get("remaining", "")),
        )

    def submit(self, answer: str) -> bool:
        result = self._call(Method.SUBMIT, {"answer": answer})
        return bool(result.get("accepted", False))
