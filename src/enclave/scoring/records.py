from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from enclave.constants import DIGEST_DOMAIN_INSTANCE

__all__ = ["CallCost", "InstanceRecord", "Outcome", "PairedRecord", "Verdict"]


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NO_DECISION = "no_decision"


class Outcome(str, Enum):
    SOLVED = "solved"

    UNSOLVED = "unsolved"
    CAP_EXHAUSTED = "cap_exhausted"
    TIMEOUT = "timeout"
    CRASHED = "crashed"
    PROTOCOL_VIOLATION = "protocol_violation"
    GRADER_TAMPERED = "grader_tampered"

    RELAY_UNAVAILABLE = "relay_unavailable"
    PROVIDER_ERROR = "provider_error"
    ENVIRONMENT_FAULT = "environment_fault"
    EVIDENCE_UNREOPENABLE = "evidence_unreopenable"

    @property
    def verdict(self) -> Verdict:
        if self is Outcome.SOLVED:
            return Verdict.PASS
        if self in _INFRASTRUCTURE_FAULTS:
            return Verdict.NO_DECISION
        return Verdict.FAIL

    @property
    def solved(self) -> bool:
        return self is Outcome.SOLVED

    @property
    def scoreable(self) -> bool:
        return self.verdict is not Verdict.NO_DECISION


_INFRASTRUCTURE_FAULTS = frozenset(
    {
        Outcome.RELAY_UNAVAILABLE,
        Outcome.PROVIDER_ERROR,
        Outcome.ENVIRONMENT_FAULT,
        Outcome.EVIDENCE_UNREOPENABLE,
    }
)


@dataclass(frozen=True, slots=True)
class CallCost:
    model: str
    provider: str
    quantization: str
    input_tokens: int
    output_tokens: int
    input_price_per_mtok: Decimal
    output_price_per_mtok: Decimal

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts must be non-negative")

    @property
    def dollars(self) -> Decimal:
        return (
            self.input_tokens * self.input_price_per_mtok
            + self.output_tokens * self.output_price_per_mtok
        ) / Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class InstanceRecord:
    instance_id: str
    submission_id: str
    round_seed: str
    environment: str
    outcome: Outcome
    spend: Decimal
    calls: tuple[CallCost, ...] = ()
    audited: bool = False
    audit_passed: bool | None = None
    reference_solved: bool | None = None
    reference_spend: Decimal | None = None
    transcript_digest: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.spend < 0:
            raise ValueError(f"spend must be non-negative, got {self.spend}")
        if self.audited and self.audit_passed is None:
            raise ValueError("audited records must carry an audit_passed verdict")
        if not self.audited and self.audit_passed is not None:
            raise ValueError("audit_passed set on a record that was not audited")

    @property
    def verdict(self) -> Verdict:
        return self.outcome.verdict

    @property
    def solved(self) -> bool:
        return self.outcome.solved

    @property
    def scoreable(self) -> bool:
        return self.outcome.scoreable

    def canonical_json(self) -> str:
        payload: dict[str, Any] = {
            "instance_id": self.instance_id,
            "submission_id": self.submission_id,
            "round_seed": self.round_seed,
            "environment": self.environment,
            "outcome": self.outcome.value,
            "spend": str(self.spend),
            "calls": [
                {
                    "model": c.model,
                    "provider": c.provider,
                    "quantization": c.quantization,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "input_price_per_mtok": str(c.input_price_per_mtok),
                    "output_price_per_mtok": str(c.output_price_per_mtok),
                }
                for c in self.calls
            ],
            "audited": self.audited,
            "audit_passed": self.audit_passed,
            "reference_solved": self.reference_solved,
            "reference_spend": None if self.reference_spend is None else str(self.reference_spend),
            "transcript_digest": self.transcript_digest,
            "metadata": self.metadata,
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False
        )

    def digest(self) -> str:
        payload = f"{DIGEST_DOMAIN_INSTANCE}\0{self.canonical_json()}"
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PairedRecord:
    agent: InstanceRecord
    reference: InstanceRecord

    def __post_init__(self) -> None:
        if self.agent.instance_id != self.reference.instance_id:
            raise ValueError(
                "paired records must share an instance_id: "
                f"{self.agent.instance_id} != {self.reference.instance_id}"
            )
        if self.agent.round_seed != self.reference.round_seed:
            raise ValueError("paired records must share a round_seed")

    @property
    def scoreable(self) -> bool:
        return self.agent.scoreable and self.reference.scoreable

    @property
    def concordance(self) -> str:
        agent_solved, reference_solved = self.agent.solved, self.reference.solved
        if agent_solved and reference_solved:
            return "both"
        if agent_solved and not reference_solved:
            return "agent_only"
        if reference_solved and not agent_solved:
            return "reference_only"
        return "neither"
