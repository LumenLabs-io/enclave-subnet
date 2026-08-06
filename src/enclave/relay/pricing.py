from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from enclave.constants import (
    DIGEST_DOMAIN_PRICE_SNAPSHOT,
    MONEY_QUANTUM,
    PRICE_SNAPSHOT_SCHEMA_VERSION,
)
from enclave.errors import PricingError

__all__ = [
    "OBSERVATION_CHANNEL",
    "ModelPrice",
    "PriceSnapshot",
    "price_tokens",
]

OBSERVATION_CHANNEL = "environment.observation"


@dataclass(frozen=True, slots=True)
class ModelPrice:
    model: str
    provider: str
    quantization: str
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    context_window: int

    def __post_init__(self) -> None:
        if not self.model:
            raise PricingError("model identifier must not be empty")
        if self.input_per_mtok < 0 or self.output_per_mtok < 0:
            raise PricingError(f"negative price for {self.model}")
        if self.context_window <= 0:
            raise PricingError(f"context_window must be positive for {self.model}")

    def as_json(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "quantization": self.quantization,
            "input_per_mtok": str(self.input_per_mtok),
            "output_per_mtok": str(self.output_per_mtok),
            "context_window": self.context_window,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> ModelPrice:
        try:
            return cls(
                model=str(payload["model"]),
                provider=str(payload["provider"]),
                quantization=str(payload["quantization"]),
                input_per_mtok=Decimal(str(payload["input_per_mtok"])),
                output_per_mtok=Decimal(str(payload["output_per_mtok"])),
                context_window=int(payload["context_window"]),
            )
        except KeyError as exc:
            raise PricingError(f"price entry missing field {exc}") from exc


def price_tokens(price: ModelPrice, input_tokens: int, output_tokens: int) -> Decimal:
    if input_tokens < 0 or output_tokens < 0:
        raise PricingError("token counts must be non-negative")
    exact = (
        input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok
    ) / Decimal(1_000_000)
    return exact.quantize(MONEY_QUANTUM)


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    snapshot_id: str
    taken_at: str
    entries: tuple[ModelPrice, ...]
    schema_version: int = PRICE_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.entries:
            raise PricingError("a price snapshot must carry at least one model")
        seen: set[str] = set()
        for entry in self.entries:
            if entry.model in seen:
                raise PricingError(f"duplicate model in snapshot: {entry.model}")
            seen.add(entry.model)
        if OBSERVATION_CHANNEL not in seen:
            raise PricingError(
                f"snapshot must price the observation channel {OBSERVATION_CHANNEL!r}; "
                "observations are billed as tokens"
            )

    @property
    def models(self) -> tuple[str, ...]:
        return tuple(sorted(entry.model for entry in self.entries))

    @property
    def selectable(self) -> tuple[str, ...]:
        return tuple(model for model in self.models if model != OBSERVATION_CHANNEL)

    def lookup(self, model: str) -> ModelPrice:
        for entry in self.entries:
            if entry.model == model:
                return entry
        raise PricingError(f"model {model!r} is not priced by snapshot {self.snapshot_id}")

    def observation_price(self) -> ModelPrice:
        return self.lookup(OBSERVATION_CHANNEL)

    def cheapest(self) -> ModelPrice:
        candidates = [e for e in self.entries if e.model != OBSERVATION_CHANNEL]
        if not candidates:
            raise PricingError("snapshot prices no selectable model")
        return min(candidates, key=lambda e: (e.input_per_mtok, e.output_per_mtok, e.model))

    def canonical_json(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "taken_at": self.taken_at,
            "entries": [entry.as_json() for entry in sorted(self.entries, key=lambda e: e.model)],
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False
        )

    def digest(self) -> str:
        payload = f"{DIGEST_DOMAIN_PRICE_SNAPSHOT}\0{self.canonical_json()}"
        return hashlib.sha256(payload.encode()).hexdigest()

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> PriceSnapshot:
        version = int(payload.get("schema_version", PRICE_SNAPSHOT_SCHEMA_VERSION))
        if version != PRICE_SNAPSHOT_SCHEMA_VERSION:
            raise PricingError(
                f"price snapshot schema {version} is not {PRICE_SNAPSHOT_SCHEMA_VERSION}"
            )
        raw: Iterable[Mapping[str, Any]] = payload.get("entries", ())
        return cls(
            snapshot_id=str(payload["snapshot_id"]),
            taken_at=str(payload["taken_at"]),
            entries=tuple(ModelPrice.from_json(entry) for entry in raw),
            schema_version=version,
        )
