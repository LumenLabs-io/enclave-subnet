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

MAX_FRAME_BYTES: Final = 1024 * 1024
MAX_JSON_DEPTH: Final = 24
MAX_JSON_ITEMS: Final = 50_000


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


def _reject_constant(name: str) -> Any:
    raise ProtocolError(f"frame carries the non-finite constant {name}")


def encode(response: Response) -> bytes:
    payload = json.dumps(
        response.as_json(), separators=(",", ":"), allow_nan=False, ensure_ascii=False
    )
    return payload.encode() + b"\n"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ProtocolError(f"duplicate key {key!r} in frame")
        seen[key] = value
    return seen


def _check_shape(value: Any, depth: int = 0, budget: list[int] | None = None) -> None:
    if budget is None:
        budget = [MAX_JSON_ITEMS]
    if depth > MAX_JSON_DEPTH:
        raise ProtocolError(f"frame nests deeper than {MAX_JSON_DEPTH}")
    budget[0] -= 1
    if budget[0] < 0:
        raise ProtocolError(f"frame carries more than {MAX_JSON_ITEMS} items")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 256:
                raise ProtocolError("object keys must be strings of at most 256 characters")
            _check_shape(item, depth + 1, budget)
    elif isinstance(value, list):
        for item in value:
            _check_shape(item, depth + 1, budget)


def decode(line: bytes) -> Request:
    if len(line) > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
    try:
        payload = json.loads(
            line,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"frame is not valid JSON: {exc}") from exc
    except RecursionError as exc:
        raise ProtocolError("frame nests too deeply to parse") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("frame must be a JSON object")
    _check_shape(payload)

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
