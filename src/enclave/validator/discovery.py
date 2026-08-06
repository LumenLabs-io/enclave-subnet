from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from enclave.chain.client import ChainClient
from enclave.chain.commitment import SubmissionCommitment, SubmissionReveal, verify_reveal
from enclave.chain.payment import PaymentProof, PaymentReader, PaymentRejected, verify_payment
from enclave.sandbox.spec import DIGEST_PATTERN
from enclave.validator.round import Submission

__all__ = ["Candidate", "discover", "parse_reveal"]


@dataclass(frozen=True, slots=True)
class Candidate:
    hotkey: str
    image: str
    admitted: bool
    rejection: str = ""

    def as_submission(self) -> Submission:
        return Submission(submission_id=self.hotkey, image=self.image)


def parse_reveal(hotkey: str, raw: str) -> SubmissionReveal | None:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    image = str(payload.get("image", ""))
    nonce = str(payload.get("nonce", ""))
    if not DIGEST_PATTERN.match(image) or len(nonce) != 32:
        return None

    proof: PaymentProof | None = None
    raw_payment = payload.get("payment")
    if isinstance(raw_payment, dict):
        try:
            proof = PaymentProof(
                block_hash=str(raw_payment.get("block_hash", "")),
                block_number=int(raw_payment.get("block_number", 0)),
                extrinsic_index=int(raw_payment.get("extrinsic_index", 0)),
            )
        except (PaymentRejected, TypeError, ValueError):
            return None

    try:
        return SubmissionReveal(
            hotkey=hotkey,
            image=image,
            nonce=nonce,
            source_url=str(payload.get("source_url", "")),
            licence=str(payload.get("licence", "")),
            payment=proof,
        )
    except Exception:
        return None


def discover(
    chain: ChainClient,
    treasury: str,
    upload_fee_rao: int,
    payment_reader: PaymentReader | None = None,
    commitments: Mapping[str, str] | None = None,
) -> tuple[Candidate, ...]:
    raw: Mapping[str, str] = commitments if commitments is not None else chain.commitments()
    registered = set(chain.metagraph().hotkeys())

    spent: set[str] = set()
    candidates: list[Candidate] = []

    for hotkey in sorted(raw):
        if hotkey not in registered:
            continue

        reveal = parse_reveal(hotkey, raw[hotkey])
        if reveal is None:
            candidates.append(
                Candidate(hotkey=hotkey, image="", admitted=False, rejection="unreadable reveal")
            )
            continue

        if not verify_reveal(
            SubmissionCommitment(hotkey=hotkey, commitment=reveal.commitment()), reveal
        ):
            candidates.append(
                Candidate(
                    hotkey=hotkey, image=reveal.image, admitted=False,
                    rejection="reveal does not open its commitment",
                )
            )
            continue

        if upload_fee_rao > 0:
            if reveal.payment is None:
                candidates.append(
                    Candidate(
                        hotkey=hotkey, image=reveal.image, admitted=False,
                        rejection="no payment proof",
                    )
                )
                continue
            if payment_reader is None:
                candidates.append(
                    Candidate(
                        hotkey=hotkey, image=reveal.image, admitted=False,
                        rejection="no payment reader configured to check the fee",
                    )
                )
                continue
            try:
                verify_payment(
                    reader=payment_reader,
                    proof=reveal.payment,
                    hotkey=hotkey,
                    treasury=treasury,
                    amount_rao=upload_fee_rao,
                    spent=frozenset(spent),
                )
            except PaymentRejected as exc:
                candidates.append(
                    Candidate(
                        hotkey=hotkey, image=reveal.image, admitted=False, rejection=str(exc)
                    )
                )
                continue
            spent.add(reveal.payment.key)

        candidates.append(Candidate(hotkey=hotkey, image=reveal.image, admitted=True))

    return tuple(candidates)


def admitted(candidates: Iterable[Candidate]) -> tuple[Submission, ...]:
    return tuple(c.as_submission() for c in candidates if c.admitted)


def rejected(candidates: Iterable[Candidate]) -> tuple[tuple[str, str], ...]:
    return tuple((c.hotkey, c.rejection) for c in candidates if not c.admitted)


def summary(candidates: Iterable[Candidate]) -> dict[str, Any]:
    items = list(candidates)
    return {
        "seen": len(items),
        "admitted": sum(1 for c in items if c.admitted),
        "rejected": sum(1 for c in items if not c.admitted),
    }
