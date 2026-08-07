from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from enclave.errors import LedgerError
from enclave.ledger.header import RoundHeader
from enclave.scoring.records import CallCost, InstanceRecord, Outcome

__all__ = ["GENESIS", "ChainedRecord", "Ledger"]

GENESIS = "0" * 64

_HEADER_FILE = "header.json"
_RECORDS_FILE = "records.jsonl"


@dataclass(frozen=True, slots=True)
class ChainedRecord:
    record: InstanceRecord
    previous: str
    link: str

    @staticmethod
    def compute_link(previous: str, record: InstanceRecord) -> str:
        return hashlib.sha256(f"{previous}\0{record.digest()}".encode()).hexdigest()

    def verify(self) -> bool:
        return self.link == ChainedRecord.compute_link(self.previous, self.record)


def _decode_record(payload: dict[str, Any]) -> InstanceRecord:
    calls = tuple(
        CallCost(
            model=str(c["model"]),
            provider=str(c["provider"]),
            quantization=str(c["quantization"]),
            input_tokens=int(c["input_tokens"]),
            output_tokens=int(c["output_tokens"]),
            input_price_per_mtok=Decimal(str(c["input_price_per_mtok"])),
            output_price_per_mtok=Decimal(str(c["output_price_per_mtok"])),
        )
        for c in payload.get("calls", ())
    )
    reference_spend = payload.get("reference_spend")
    return InstanceRecord(
        instance_id=str(payload["instance_id"]),
        submission_id=str(payload["submission_id"]),
        round_seed=str(payload["round_seed"]),
        environment=str(payload["environment"]),
        outcome=Outcome(str(payload["outcome"])),
        spend=Decimal(str(payload["spend"])),
        calls=calls,
        audited=bool(payload.get("audited", False)),
        audit_passed=payload.get("audit_passed"),
        reference_solved=payload.get("reference_solved"),
        reference_spend=None if reference_spend is None else Decimal(str(reference_spend)),
        transcript_digest=str(payload.get("transcript_digest", "")),
        metadata=dict(payload.get("metadata", {})),
    )


class Ledger:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _round_dir(self, round_id: str) -> Path:
        if not round_id or "/" in round_id or ".." in round_id:
            raise LedgerError(f"unsafe round identifier {round_id!r}")
        return self._root / round_id

    def rounds(self) -> tuple[str, ...]:
        return tuple(
            sorted(p.name for p in self._root.iterdir() if (p / _HEADER_FILE).exists())
        )

    def open_round(self, header: RoundHeader) -> None:
        directory = self._round_dir(header.round_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / _HEADER_FILE
        if path.exists():
            existing = RoundHeader.from_json(json.loads(path.read_text(encoding="utf-8")))
            if existing.digest() != header.digest():
                raise LedgerError(
                    f"round {header.round_id} already exists with a different header"
                )
            return
        path.write_text(header.canonical_json(), encoding="utf-8")

    def header(self, round_id: str) -> RoundHeader:
        path = self._round_dir(round_id) / _HEADER_FILE
        if not path.exists():
            raise LedgerError(f"round {round_id} has no header")
        return RoundHeader.from_json(json.loads(path.read_text(encoding="utf-8")))

    def tip(self, round_id: str) -> str:
        previous = GENESIS
        for chained in self.chained(round_id):
            previous = chained.link
        return previous

    def append(self, round_id: str, record: InstanceRecord) -> ChainedRecord:
        directory = self._round_dir(round_id)
        if not (directory / _HEADER_FILE).exists():
            raise LedgerError(f"cannot append to unopened round {round_id}")

        previous = self.tip(round_id)
        link = ChainedRecord.compute_link(previous, record)
        line = json.dumps(
            {
                "previous": previous,
                "link": link,
                "record": json.loads(record.canonical_json()),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        with (directory / _RECORDS_FILE).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return ChainedRecord(record=record, previous=previous, link=link)

    def chained(self, round_id: str) -> Iterator[ChainedRecord]:
        path = self._round_dir(round_id) / _RECORDS_FILE
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                payload = json.loads(line)
                yield ChainedRecord(
                    record=_decode_record(payload["record"]),
                    previous=str(payload["previous"]),
                    link=str(payload["link"]),
                )

    def records(self, round_id: str) -> tuple[InstanceRecord, ...]:
        return tuple(chained.record for chained in self.chained(round_id))

    def verify(self, round_id: str) -> bool:
        previous = GENESIS
        for chained in self.chained(round_id):
            if chained.previous != previous or not chained.verify():
                return False
            previous = chained.link
        return True

    def submissions(self, round_id: str) -> tuple[str, ...]:
        return tuple(sorted({r.submission_id for r in self.records(round_id)}))

    def for_submission(self, round_id: str, submission_id: str) -> tuple[InstanceRecord, ...]:
        return tuple(r for r in self.records(round_id) if r.submission_id == submission_id)

    def history(self, submission_id: str, rounds: Sequence[str]) -> tuple[InstanceRecord, ...]:
        collected: list[InstanceRecord] = []
        for round_id in rounds:
            collected.extend(self.for_submission(round_id, submission_id))
        return tuple(collected)
