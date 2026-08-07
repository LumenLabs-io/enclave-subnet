from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from enclave.constants import (
    AUDIT_RATE,
    DENOMINATOR_FLOOR,
    DIGEST_DOMAIN_ROUND,
    FAILURE_PENALTY,
    ROUND_SCHEMA_VERSION,
    SPEND_CAP,
)
from enclave.errors import ProtocolError
from enclave.scoring.yield_score import ScoringParams

__all__ = ["RoundHeader"]


@dataclass(frozen=True, slots=True)
class RoundHeader:
    round_id: str
    seed: str
    opened_at: str
    price_snapshot_digest: str
    audit_commitment: str
    families: tuple[str, ...]
    instances_per_family: int
    spend_cap: Decimal = SPEND_CAP
    failure_penalty: Decimal = FAILURE_PENALTY
    denominator_floor: Decimal = DENOMINATOR_FLOOR
    audit_rate: Decimal = AUDIT_RATE
    schema_version: int = ROUND_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.seed:
            raise ProtocolError("a round header requires a seed")
        if not self.families:
            raise ProtocolError("a round header requires at least one family")
        if self.instances_per_family < 1:
            raise ProtocolError("instances_per_family must be at least 1")
        if not 0 <= self.audit_rate <= 1:
            raise ProtocolError("audit_rate must lie in [0, 1]")
        if len(self.audit_commitment) != 64:
            raise ProtocolError("audit_commitment must be a sha256 hex digest")

    @property
    def scoring_params(self) -> ScoringParams:
        return ScoringParams(
            spend_cap=self.spend_cap,
            failure_penalty=self.failure_penalty,
            denominator_floor=self.denominator_floor,
        )

    @property
    def instance_count(self) -> int:
        return len(self.families) * self.instances_per_family

    def canonical_json(self) -> str:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "round_id": self.round_id,
            "seed": self.seed,
            "opened_at": self.opened_at,
            "price_snapshot_digest": self.price_snapshot_digest,
            "audit_commitment": self.audit_commitment,
            "families": list(self.families),
            "instances_per_family": self.instances_per_family,
            "spend_cap": str(self.spend_cap),
            "failure_penalty": str(self.failure_penalty),
            "denominator_floor": str(self.denominator_floor),
            "audit_rate": str(self.audit_rate),
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False
        )

    def digest(self) -> str:
        payload = f"{DIGEST_DOMAIN_ROUND}\0{self.canonical_json()}"
        return hashlib.sha256(payload.encode()).hexdigest()

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> RoundHeader:
        version = int(payload.get("schema_version", ROUND_SCHEMA_VERSION))
        if version != ROUND_SCHEMA_VERSION:
            raise ProtocolError(f"round schema {version} is not {ROUND_SCHEMA_VERSION}")
        return cls(
            round_id=str(payload["round_id"]),
            seed=str(payload["seed"]),
            opened_at=str(payload["opened_at"]),
            price_snapshot_digest=str(payload["price_snapshot_digest"]),
            audit_commitment=str(payload["audit_commitment"]),
            families=tuple(str(f) for f in payload["families"]),
            instances_per_family=int(payload["instances_per_family"]),
            spend_cap=Decimal(str(payload["spend_cap"])),
            failure_penalty=Decimal(str(payload["failure_penalty"])),
            denominator_floor=Decimal(str(payload["denominator_floor"])),
            audit_rate=Decimal(str(payload["audit_rate"])),
            schema_version=version,
        )
