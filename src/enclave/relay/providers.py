from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from enclave.errors import RelayError

__all__ = [
    "Completion",
    "DeterministicProvider",
    "HeuristicTokenCounter",
    "Message",
    "Provider",
    "TokenCounter",
    "render_messages",
]


@dataclass(frozen=True, slots=True)
class Message:
    role: str
    content: str

    @classmethod
    def from_json(cls, payload: Any) -> Message:
        if not isinstance(payload, dict):
            raise RelayError("each message must be a JSON object")
        role = payload.get("role")
        content = payload.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise RelayError("message requires string role and content")
        return cls(role=role, content=content)


@dataclass(frozen=True, slots=True)
class Completion:
    content: str
    reported_input_tokens: int | None = None
    reported_output_tokens: int | None = None


@runtime_checkable
class Provider(Protocol):
    async def complete(
        self,
        model: str,
        messages: Sequence[Message],
        max_tokens: int,
        stop: Sequence[str],
    ) -> Completion: ...


@runtime_checkable
class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


_WORD = re.compile(r"\w+|[^\w\s]")


class HeuristicTokenCounter:
    def __init__(self, chars_per_token: int = 4) -> None:
        if chars_per_token < 1:
            raise RelayError("chars_per_token must be at least 1")
        self._chars_per_token = chars_per_token

    def count(self, text: str) -> int:
        if not text:
            return 0
        pieces = len(_WORD.findall(text))
        by_length = (len(text) + self._chars_per_token - 1) // self._chars_per_token
        return max(pieces, by_length, 1)


def render_messages(messages: Sequence[Message]) -> str:
    return "\n".join(f"{m.role}: {m.content}" for m in messages)


class DeterministicProvider:
    def __init__(self, seed: str, counter: TokenCounter | None = None) -> None:
        self._seed = seed
        self._counter = counter or HeuristicTokenCounter()

    async def complete(
        self,
        model: str,
        messages: Sequence[Message],
        max_tokens: int,
        stop: Sequence[str],
    ) -> Completion:
        material = f"{self._seed}\0{model}\0{render_messages(messages)}"
        digest = hashlib.sha256(material.encode()).hexdigest()
        content = f"deterministic:{digest[:32]}"
        return Completion(
            content=content,
            reported_input_tokens=self._counter.count(render_messages(messages)),
            reported_output_tokens=self._counter.count(content),
        )
