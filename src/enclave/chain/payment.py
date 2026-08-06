from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from enclave.constants import RAO_PER_TAO, UPLOAD_FEE_RAO
from enclave.errors import ChainError

__all__ = [
    "PaymentProof",
    "PaymentReader",
    "PaymentRejected",
    "Transfer",
    "fee_tao",
    "verify_payment",
]

_SS58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{47,48}$")
_BLOCK_HASH = re.compile(r"^0x[0-9a-f]{64}$")


class PaymentRejected(ChainError):
    pass


def fee_tao(rao: int = UPLOAD_FEE_RAO) -> Decimal:
    return Decimal(rao) / Decimal(RAO_PER_TAO)


@dataclass(frozen=True, slots=True)
class PaymentProof:
    block_hash: str
    block_number: int
    extrinsic_index: int

    def __post_init__(self) -> None:
        if not _BLOCK_HASH.match(self.block_hash):
            raise PaymentRejected(
                f"block_hash must be 0x and 64 hex chars, got {self.block_hash!r}"
            )
        if self.block_number < 0:
            raise PaymentRejected("block_number must be non-negative")
        if self.extrinsic_index < 0:
            raise PaymentRejected("extrinsic_index must be non-negative")

    @property
    def key(self) -> str:
        return f"{self.block_hash}:{self.extrinsic_index}"

    def as_json(self) -> dict[str, Any]:
        return {
            "block_hash": self.block_hash,
            "block_number": self.block_number,
            "extrinsic_index": self.extrinsic_index,
        }


@dataclass(frozen=True, slots=True)
class Transfer:
    sender: str
    destination: str
    amount_rao: int
    call: str
    succeeded: bool
    block_hash: str
    block_number: int
    extrinsic_index: int


@runtime_checkable
class PaymentReader(Protocol):
    def canonical_block_hash(self, block_number: int) -> str: ...

    def transfer_at(self, block_hash: str, extrinsic_index: int) -> Transfer | None: ...

    def coldkey_of(self, hotkey: str, block_hash: str) -> str | None: ...


def verify_payment(
    reader: PaymentReader,
    proof: PaymentProof,
    hotkey: str,
    treasury: str,
    amount_rao: int = UPLOAD_FEE_RAO,
    spent: frozenset[str] = frozenset(),
) -> Transfer:
    if not _SS58.match(treasury):
        raise PaymentRejected(f"treasury address is not a valid ss58: {treasury!r}")
    if proof.key in spent:
        raise PaymentRejected(f"payment {proof.key} was already used by another submission")

    canonical = reader.canonical_block_hash(proof.block_number)
    if canonical != proof.block_hash:
        raise PaymentRejected(
            f"block {proof.block_number} is {canonical}, not the claimed {proof.block_hash}"
        )

    transfer = reader.transfer_at(proof.block_hash, proof.extrinsic_index)
    if transfer is None:
        raise PaymentRejected(
            f"no extrinsic at index {proof.extrinsic_index} in {proof.block_hash}"
        )
    if not transfer.succeeded:
        raise PaymentRejected("the payment extrinsic did not succeed on chain")
    if transfer.call != "Balances.transfer_keep_alive":
        raise PaymentRejected(
            f"payment must be Balances.transfer_keep_alive, got {transfer.call}"
        )
    if transfer.destination != treasury:
        raise PaymentRejected(
            f"payment went to {transfer.destination}, not the published treasury {treasury}"
        )
    if transfer.amount_rao != amount_rao:
        raise PaymentRejected(
            f"payment is {transfer.amount_rao} rao, expected exactly {amount_rao} "
            f"({fee_tao(amount_rao)} TAO)"
        )

    owner = reader.coldkey_of(hotkey, proof.block_hash)
    if owner is None:
        raise PaymentRejected(f"hotkey {hotkey[:12]} had no owner at the payment block")
    if owner != transfer.sender:
        raise PaymentRejected(
            f"payment was signed by {transfer.sender[:12]}, which did not own "
            f"hotkey {hotkey[:12]} at that block"
        )

    return transfer
