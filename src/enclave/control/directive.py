from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from enclave.errors import ConfigError, PricingError, ProtocolError
from enclave.relay.pricing import PriceSnapshot

__all__ = [
    "DIRECTIVE_DOMAIN",
    "Directive",
    "canonical_json",
    "digest_of",
    "signing_message",
    "verify_directive",
]

DIRECTIVE_DOMAIN: Final = "enclave.directive.v1"


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False
    )


def digest_of(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(f"{DIRECTIVE_DOMAIN}\0{canonical_json(payload)}".encode()).hexdigest()


def signing_message(payload: Mapping[str, Any]) -> bytes:
    return f"{DIRECTIVE_DOMAIN}\0{canonical_json(payload)}".encode()


@dataclass(frozen=True, slots=True)
class Directive:
    revision: int
    netuid: int
    effective_from_block: int
    treasury: str
    upload_fee_rao: int
    emissions_paused: bool
    pause_reason: str
    min_mechanism_version: int
    families: tuple[str, ...]
    instances_per_family: int
    spend_cap: Decimal
    failure_penalty: Decimal
    denominator_floor: Decimal
    audit_rate: Decimal
    reserved_hotkey: str
    reserved_share: Decimal
    price_snapshot: PriceSnapshot
    default_model: str
    digest: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Directive:
        raw_snapshot = payload.get("price_snapshot")
        if not isinstance(raw_snapshot, Mapping):
            raise ProtocolError(
                "directive carries no price_snapshot; the model schedule is the denominator "
                "of every yield, so it must arrive signed rather than from local configuration"
            )
        try:
            snapshot = PriceSnapshot.from_json(raw_snapshot)
        except PricingError as exc:
            raise ProtocolError(f"directive price snapshot is invalid: {exc}") from exc

        default_model = str(payload.get("default_model", ""))
        if default_model not in snapshot.selectable:
            raise ProtocolError(
                f"directive default_model {default_model!r} is not priced by snapshot "
                f"{snapshot.snapshot_id}; priced models are {', '.join(snapshot.selectable)}"
            )

        try:
            return cls(
                revision=int(payload["revision"]),
                netuid=int(payload.get("netuid", 92)),
                effective_from_block=int(payload.get("effective_from_block", 0)),
                treasury=str(payload["treasury"]),
                upload_fee_rao=int(payload.get("upload_fee_rao", 0)),
                emissions_paused=bool(payload.get("emissions_paused", False)),
                pause_reason=str(payload.get("pause_reason", "")),
                min_mechanism_version=int(payload.get("min_mechanism_version", 0)),
                families=tuple(str(f) for f in payload.get("families", ())),
                instances_per_family=int(payload.get("instances_per_family", 1)),
                spend_cap=Decimal(str(payload.get("spend_cap", "0"))),
                failure_penalty=Decimal(str(payload.get("failure_penalty", "0"))),
                denominator_floor=Decimal(str(payload.get("denominator_floor", "0.005"))),
                audit_rate=Decimal(str(payload.get("audit_rate", "0"))),
                reserved_hotkey=str(payload.get("reserved_hotkey", "")),
                reserved_share=Decimal(str(payload.get("reserved_share", "0"))),
                price_snapshot=snapshot,
                default_model=default_model,
                digest=digest_of(payload),
            )
        except (KeyError, ValueError, ArithmeticError) as exc:
            raise ProtocolError(f"directive payload is malformed: {exc}") from exc

    def effective_at(self, block: int) -> bool:
        """Whether this revision governs scoring at ``block``.

        Prices and scoring parameters must change on the same block for every validator,
        not whenever each one's poll happens to land, so a revision published ahead of its
        block is held until the chain reaches it.
        """
        return block >= self.effective_from_block

    def assert_runnable(self, mechanism_version: int, netuid: int) -> None:
        if self.netuid != netuid:
            raise ConfigError(
                f"directive is for netuid {self.netuid} but this validator serves {netuid}"
            )
        if mechanism_version < self.min_mechanism_version:
            raise ConfigError(
                f"this validator runs mechanism {mechanism_version} but the network requires "
                f"at least {self.min_mechanism_version}; update before scoring another round"
            )


def verify_directive(
    payload: Mapping[str, Any],
    signature: str,
    owner_public_key: str,
) -> Directive:
    if not owner_public_key:
        raise ConfigError(
            "no owner public key is compiled in, so no directive can be trusted; "
            "refusing to accept network parameters from an unauthenticated source"
        )
    if not signature:
        raise ProtocolError("directive carries no signature")

    try:
        from substrateinterface import Keypair
    except ImportError as exc:
        raise ConfigError("substrate-interface is required to verify a directive") from exc

    raw = signature[2:] if signature.startswith("0x") else signature
    try:
        verified = bool(
            Keypair(ss58_address=owner_public_key).verify(
                signing_message(payload), bytes.fromhex(raw)
            )
        )
    except Exception as exc:
        raise ProtocolError(f"directive signature could not be checked: {exc}") from exc

    if not verified:
        raise ProtocolError(
            "directive signature does not verify against the owner key; "
            "the control plane may be compromised or impersonated"
        )
    return Directive.from_payload(payload)
