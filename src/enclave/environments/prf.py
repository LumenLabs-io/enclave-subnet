from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from enclave.constants import (
    AUDIT_SECRET_MIN_BYTES,
    DIGEST_DOMAIN_AUDIT_SECRET,
    DIGEST_DOMAIN_AUDIT_SELECTION,
    DIGEST_DOMAIN_AUDIT_TRANSFORM,
)
from enclave.errors import AuditError

__all__ = ["AuditKey", "SeededRandom", "derive_seed", "unit_interval"]

_UNIT_BITS: Final = 64
_UNIT_DENOMINATOR: Final = 1 << _UNIT_BITS


def derive_seed(*parts: str) -> str:
    material = "\0".join(parts)
    return hashlib.sha256(material.encode()).hexdigest()


def unit_interval(digest: bytes) -> Decimal:
    value = int.from_bytes(digest[: _UNIT_BITS // 8], "big")
    return Decimal(value) / Decimal(_UNIT_DENOMINATOR)


@dataclass(frozen=True, slots=True)
class AuditKey:
    secret: bytes

    def __post_init__(self) -> None:
        if len(self.secret) < AUDIT_SECRET_MIN_BYTES:
            raise AuditError(
                f"audit secret must be at least {AUDIT_SECRET_MIN_BYTES} bytes, "
                f"got {len(self.secret)}"
            )

    @classmethod
    def generate(cls) -> AuditKey:
        return cls(secret=secrets.token_bytes(AUDIT_SECRET_MIN_BYTES))

    @classmethod
    def from_hex(cls, value: str) -> AuditKey:
        try:
            return cls(secret=bytes.fromhex(value))
        except ValueError as exc:
            raise AuditError("audit secret must be hex") from exc

    def hex(self) -> str:
        return self.secret.hex()

    def commitment(self) -> str:
        payload = DIGEST_DOMAIN_AUDIT_SECRET.encode() + b"\0" + self.secret
        return hashlib.sha256(payload).hexdigest()

    def verify_commitment(self, commitment: str) -> bool:
        return hmac.compare_digest(self.commitment(), commitment)

    def _mac(self, domain: str, *parts: str) -> bytes:
        message = "\0".join((domain, *parts)).encode()
        return hmac.new(self.secret, message, hashlib.sha256).digest()

    def selects(self, round_seed: str, instance_id: str, rate: Decimal) -> bool:
        if not 0 <= rate <= 1:
            raise AuditError("audit rate must lie in [0, 1]")
        if rate == 0:
            return False
        draw = unit_interval(self._mac(DIGEST_DOMAIN_AUDIT_SELECTION, round_seed, instance_id))
        return draw < rate

    def transform_seed(self, round_seed: str, instance_id: str) -> str:
        return self._mac(DIGEST_DOMAIN_AUDIT_TRANSFORM, round_seed, instance_id).hex()


class SeededRandom:
    def __init__(self, seed: str) -> None:
        self._seed = seed
        self._counter = 0

    @property
    def seed(self) -> str:
        return self._seed

    def _next(self) -> bytes:
        material = f"{self._seed}\0{self._counter}".encode()
        self._counter += 1
        return hashlib.sha256(material).digest()

    def unit(self) -> Decimal:
        return unit_interval(self._next())

    def below(self, bound: int) -> int:
        if bound <= 0:
            raise AuditError("bound must be positive")
        return int.from_bytes(self._next()[:8], "big") % bound

    def choice(self, items: list[str]) -> str:
        if not items:
            raise AuditError("cannot choose from an empty sequence")
        return items[self.below(len(items))]

    def sample(self, items: list[str], count: int) -> list[str]:
        if count > len(items):
            raise AuditError(f"cannot sample {count} of {len(items)}")
        pool = sorted(items)
        picked: list[str] = []
        for _ in range(count):
            index = self.below(len(pool))
            picked.append(pool.pop(index))
        return picked

    def shuffled(self, items: list[str]) -> list[str]:
        pool = sorted(items)
        out: list[str] = []
        while pool:
            out.append(pool.pop(self.below(len(pool))))
        return out
