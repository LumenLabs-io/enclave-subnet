from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Any

from enclave.constants import MAX_ONCHAIN_PAYLOAD_BYTES, PROTOCOL_VERSION
from enclave.errors import ChainError
from enclave.sandbox.spec import DIGEST_PATTERN

__all__ = ["SubmissionCommitment", "SubmissionReveal", "commit", "verify_reveal"]

_NONCE_BYTES = 16


@dataclass(frozen=True, slots=True)
class SubmissionCommitment:
    hotkey: str
    commitment: str
    protocol_version: int = PROTOCOL_VERSION

    def payload(self) -> str:
        body = json.dumps(
            {
                "v": self.protocol_version,
                "hotkey": self.hotkey,
                "commitment": self.commitment,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(body.encode()) > MAX_ONCHAIN_PAYLOAD_BYTES:
            raise ChainError("commitment payload exceeds the on-chain ceiling")
        return body


@dataclass(frozen=True, slots=True)
class SubmissionReveal:
    hotkey: str
    image: str
    nonce: str
    source_url: str = ""
    licence: str = ""

    def __post_init__(self) -> None:
        if not DIGEST_PATTERN.match(self.image):
            raise ChainError(f"revealed image must be digest-pinned, got {self.image!r}")
        if len(self.nonce) != _NONCE_BYTES * 2:
            raise ChainError("nonce must be 16 bytes of hex")

    def commitment(self) -> str:
        material = "\0".join(
            (str(PROTOCOL_VERSION), self.hotkey, self.image, self.nonce)
        )
        return hashlib.sha256(material.encode()).hexdigest()

    def as_json(self) -> dict[str, Any]:
        return {
            "hotkey": self.hotkey,
            "image": self.image,
            "nonce": self.nonce,
            "source_url": self.source_url,
            "licence": self.licence,
        }


def commit(hotkey: str, image: str) -> tuple[SubmissionCommitment, SubmissionReveal]:
    nonce = secrets.token_hex(_NONCE_BYTES)
    reveal = SubmissionReveal(hotkey=hotkey, image=image, nonce=nonce)
    return (
        SubmissionCommitment(hotkey=hotkey, commitment=reveal.commitment()),
        reveal,
    )


def verify_reveal(commitment: SubmissionCommitment, reveal: SubmissionReveal) -> bool:
    if commitment.hotkey != reveal.hotkey:
        return False
    return hmac.compare_digest(commitment.commitment, reveal.commitment())
