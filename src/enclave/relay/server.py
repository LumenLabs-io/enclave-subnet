from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from enclave.errors import IsolationError, ProtocolError, RelayError
from enclave.relay.meter import Meter, SpendCapExceeded
from enclave.relay.protocol import (
    ErrorCode,
    Method,
    Request,
    Response,
    decode,
    encode,
)
from enclave.relay.providers import Completion, Message, Provider, TokenCounter, render_messages

__all__ = ["ActionSink", "RelayServer", "RelaySession", "SessionState"]


@runtime_checkable
class ActionSink(Protocol):
    def act(self, action: str, arguments: Mapping[str, Any]) -> tuple[str, bool]: ...


class SessionState:
    CREATED = "created"
    READY = "ready"
    SUBMITTED = "submitted"
    TERMINATED = "terminated"


@dataclass(slots=True)
class RelaySession:
    instance_id: str
    seed: str
    environment_name: str
    meter: Meter
    provider: Provider
    counter: TokenCounter
    sink: ActionSink
    default_model: str
    deadline_seconds: int = 0
    state: str = SessionState.CREATED
    answer: str | None = None
    terminal_reached: bool = False
    violations: list[str] = field(default_factory=list)
    _actions: int = 0

    @property
    def submitted(self) -> bool:
        return self.answer is not None

    @property
    def action_count(self) -> int:
        return self._actions

    def _violation(self, detail: str) -> None:
        self.violations.append(detail)

    async def dispatch(self, request: Request) -> Response:
        params = request.sanitised()
        if len(params) != len(request.params):
            self._violation(f"stripped banned fields on {request.method}")

        if request.method == Method.INITIALISE:
            return self._initialise(request.id)
        if self.state == SessionState.CREATED:
            return Response(
                id=request.id,
                error_code=ErrorCode.OUT_OF_SEQUENCE,
                error_message="initialise must be called first",
            )
        if self.submitted:
            return Response(
                id=request.id,
                error_code=ErrorCode.ALREADY_SUBMITTED,
                error_message="the instance already received an answer",
            )
        if request.method == Method.COMPLETIONS:
            return await self._completions(request.id, params)
        if request.method == Method.ACT:
            return self._act(request.id, params)
        return self._submit(request.id, params)

    def _initialise(self, request_id: int) -> Response:
        self.state = SessionState.READY
        return Response(
            id=request_id,
            result={
                "ready": True,
                "instance_id": self.instance_id,
                "seed": self.seed,
                "environment": self.environment_name,
                "spend_cap": str(self.meter.spend_cap),
                "deadline_seconds": self.deadline_seconds,
                "models": list(self.meter.snapshot.selectable),
            },
        )

    def _select_model(self, params: Mapping[str, Any]) -> str:
        requested = params.get("model")
        if requested is None:
            return self.default_model
        if not isinstance(requested, str):
            raise RelayError("model must be a string")
        if requested not in self.meter.snapshot.selectable:
            raise RelayError(f"model {requested!r} is not priced this round")
        return requested

    async def _completions(self, request_id: int, params: Mapping[str, Any]) -> Response:
        raw_messages = params.get("messages")
        if not isinstance(raw_messages, list) or not raw_messages:
            return Response(
                id=request_id,
                error_code=ErrorCode.MALFORMED,
                error_message="messages must be a non-empty array",
            )
        try:
            messages: Sequence[Message] = [Message.from_json(m) for m in raw_messages]
            model = self._select_model(params)
        except RelayError as exc:
            code = (
                ErrorCode.UNPRICED_MODEL if "is not priced" in str(exc) else ErrorCode.MALFORMED
            )
            return Response(id=request_id, error_code=code, error_message=str(exc))

        max_tokens = int(params.get("max_tokens", 1024))
        stop_raw = params.get("stop", [])
        stop = [str(s) for s in stop_raw] if isinstance(stop_raw, list) else []

        prompt_tokens = self.counter.count(render_messages(messages))
        if not self.meter.affordable(model, prompt_tokens, max_tokens):
            return self._exhaust(request_id, "projected cost exceeds the remaining cap")

        try:
            completion: Completion = await self.provider.complete(
                model=model, messages=messages, max_tokens=max_tokens, stop=stop
            )
        except Exception as exc:
            return Response(
                id=request_id,
                error_code=ErrorCode.PROVIDER_ERROR,
                error_message=f"provider call failed: {exc}",
            )

        output_tokens = self.counter.count(completion.content)
        try:
            charge = self.meter.charge_model(
                model=model,
                input_tokens=prompt_tokens,
                output_tokens=output_tokens,
                reported_input=completion.reported_input_tokens,
                reported_output=completion.reported_output_tokens,
            )
        except SpendCapExceeded as exc:
            return self._exhaust(request_id, str(exc))

        return Response(
            id=request_id,
            result={
                "content": completion.content,
                "usage": {
                    "input_tokens": charge.cost.input_tokens,
                    "output_tokens": charge.cost.output_tokens,
                },
                "budget": {"remaining": str(charge.remaining)},
            },
        )

    def _act(self, request_id: int, params: Mapping[str, Any]) -> Response:
        action = params.get("action")
        if not isinstance(action, str) or not action:
            return Response(
                id=request_id,
                error_code=ErrorCode.MALFORMED,
                error_message="action must be a non-empty string",
            )
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return Response(
                id=request_id,
                error_code=ErrorCode.MALFORMED,
                error_message="arguments must be a JSON object",
            )

        try:
            observation, terminal = self.sink.act(action, arguments)
        except Exception as exc:
            return Response(
                id=request_id,
                error_code=ErrorCode.ENVIRONMENT_FAULT,
                error_message=f"environment refused the action: {exc}",
            )

        self._actions += 1
        self.terminal_reached = self.terminal_reached or terminal

        try:
            charge = self.meter.charge_observation(self.counter.count(observation))
        except SpendCapExceeded as exc:
            return self._exhaust(request_id, str(exc))

        return Response(
            id=request_id,
            result={
                "observation": observation,
                "terminal": terminal,
                "budget": {"remaining": str(charge.remaining)},
            },
        )

    def _submit(self, request_id: int, params: Mapping[str, Any]) -> Response:
        answer = params.get("answer")
        if not isinstance(answer, str):
            return Response(
                id=request_id,
                error_code=ErrorCode.MALFORMED,
                error_message="answer must be a string",
            )
        self.answer = answer
        self.state = SessionState.SUBMITTED
        return Response(id=request_id, result={"accepted": True})

    def _exhaust(self, request_id: int, detail: str) -> Response:
        self.state = SessionState.TERMINATED
        self.meter.seal()
        return Response(id=request_id, error_code=ErrorCode.CAP_EXHAUSTED, error_message=detail)


class RelayServer:
    def __init__(self, session: RelaySession, socket_path: Path) -> None:
        self._session = session
        self._path = socket_path
        self._server: asyncio.AbstractServer | None = None

    @property
    def session(self) -> RelaySession:
        return self._session

    async def __aenter__(self) -> RelayServer:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()
        starter = getattr(asyncio, "start_unix_server", None)
        if starter is None:
            raise IsolationError("unix domain sockets are unavailable on this platform")
        self._server = await starter(self._handle, path=str(self._path))
        os.chmod(self._path, 0o600)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                try:
                    request = decode(line)
                except ProtocolError as exc:
                    self._session.violations.append(str(exc))
                    writer.write(
                        encode(
                            Response(
                                id=0,
                                error_code=ErrorCode.MALFORMED,
                                error_message=str(exc),
                            )
                        )
                    )
                    await writer.drain()
                    continue

                response = await self._session.dispatch(request)
                writer.write(encode(response))
                await writer.drain()
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
