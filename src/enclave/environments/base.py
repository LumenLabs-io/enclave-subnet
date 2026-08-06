from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from enclave.constants import DIGEST_DOMAIN_INSTANCE
from enclave.errors import GeneratorError

__all__ = [
    "ActionSpec",
    "Environment",
    "Family",
    "Grade",
    "InstanceSpec",
    "normalise_answer",
]


def normalise_answer(value: str) -> str:
    return " ".join(value.strip().casefold().split())


@dataclass(frozen=True, slots=True)
class ActionSpec:
    name: str
    arguments: tuple[str, ...]
    description: str

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": list(self.arguments),
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class Grade:
    solved: bool
    detail: str = ""
    expected: str | None = None
    observed: str | None = None


@dataclass(frozen=True, slots=True)
class InstanceSpec:
    instance_id: str
    family: str
    seed: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def canonical_json(self) -> str:
        payload = {
            "instance_id": self.instance_id,
            "family": self.family,
            "seed": self.seed,
            "parameters": dict(sorted(self.parameters.items())),
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False
        )

    def digest(self) -> str:
        payload = f"{DIGEST_DOMAIN_INSTANCE}\0{self.canonical_json()}"
        return hashlib.sha256(payload.encode()).hexdigest()


@runtime_checkable
class Environment(Protocol):
    @property
    def family(self) -> str: ...

    @property
    def instance_id(self) -> str: ...

    def briefing(self) -> str: ...

    def actions(self) -> Sequence[ActionSpec]: ...

    def act(self, action: str, arguments: Mapping[str, Any]) -> tuple[str, bool]: ...

    def grade(self, answer: str) -> Grade: ...

    def construction_digest(self) -> str: ...


@runtime_checkable
class Family(Protocol):
    @property
    def name(self) -> str: ...

    def generate(self, spec: InstanceSpec) -> Environment: ...

    def audited(self, spec: InstanceSpec, transform_seed: str) -> Environment: ...


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise GeneratorError(detail)
