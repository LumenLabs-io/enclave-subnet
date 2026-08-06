from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final

from enclave.constants import RELAY_PROTOCOL_VERSION
from enclave.errors import ProtocolError

__all__ = [
    "SOCKET_PATH",
    "STRIPPED_FIELDS",
    "ErrorCode",
    "Method",
    "Request",
    "Response",
    "decode",
    "encode",
]

SOCKET_PATH: Final = "/enclave/relay.sock"

MAX_FRAME_BYTES: Final = 8 * 1024 * 1024


class Method:
    INITIALISE: Final = "initialise"
    COMPLETIONS: Final = "model.completions"
    ACT: Final = "env.act"
    SUBMIT: Final = "submit"

    ALL: Final = frozenset({INITIALISE, COMPLETIONS, ACT, SUBMIT})


class ErrorCode:
    MALFORMED: Final = "malformed_frame"
    UNKNOWN_METHOD: Final = "unknown_method"
    OUT_OF_SEQUENCE: Final = "out_of_sequence"
    CAP_EXHAUSTED: Final = "cap_exhausted"
    DEADLINE: Final = "deadline_exceeded"
    UNPRICED_MODEL: Final = "unpriced_model"
    PROVIDER_ERROR: Final = "provider_error"
    ENVIRONMENT_FAULT: Final = "environment_fault"
    ALREADY_SUBMITTED: Final = "already_submitted"


STRIPPED_FIELDS: Final = frozenset(
    {"provider", "quantization", "routing", "route", "temperature_seed", "api_key", "endpoint"}
)


@dataclass(frozen=True, slots=True)
class Request:
    id: int
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.method not in Method.ALL:
            raise ProtocolError(f"unknown method {self.method!r}")

    def sanitised(self) -> dict[str, Any]:
        return {k: v for k, v in self.params.items() if k not in STRIPPED_FIELDS}


@dataclass(frozen=True, slots=True)
class Response:
    id: int
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str = ""

    @property
    def ok(self) -> bool:
        return self.error_code is None

    def as_json(self) -> dict[str, Any]:
        if self.error_code is not None:
            return {
                "id": self.id,
                "protocol_version": RELAY_PROTOCOL_VERSION,
                "error": {"code": self.error_code, "message": self.error_message},
            }
        return {
            "id": self.id,
            "protocol_version": RELAY_PROTOCOL_VERSION,
            "result": self.result or {},
        }


def encode(response: Response) -> bytes:
    payload = json.dumps(
        response.as_json(), separators=(",", ":"), allow_nan=False, ensure_ascii=False
    )
    return payload.encode() + b"\n"


def decode(line: bytes) -> Request:
    if len(line) > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"frame is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("frame must be a JSON object")

    raw_id = payload.get("id")
    if not isinstance(raw_id, int):
        raise ProtocolError("frame must carry an integer id")
    method = payload.get("method")
    if not isinstance(method, str):
        raise ProtocolError("frame must carry a string method")
    params = payload.get("params", {})
    if not isinstance(params, dict):
        raise ProtocolError("params must be a JSON object")

    return Request(id=raw_id, method=method, params=params)
