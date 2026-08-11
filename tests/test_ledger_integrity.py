import json
from decimal import Decimal
from pathlib import Path

from enclave.ledger.header import RoundHeader
from enclave.ledger.store import GENESIS, Ledger
from enclave.scoring.records import CallCost, InstanceRecord, Outcome


def make_header() -> RoundHeader:
    return RoundHeader(
        round_id="test-integrity",
        seed="test-seed",
        opened_at="2026-08-11T00:00:00Z",
        price_snapshot_digest="0" * 64,
        audit_commitment="1" * 64,
        families=("archive",),
        instances_per_family=1,
        spend_cap=Decimal("2.00"),
        failure_penalty=Decimal("0.25"),
        denominator_floor=Decimal("0.005"),
        audit_rate=Decimal("0.05"),
        schema_version=1,
    )


def make_record() -> InstanceRecord:
    return InstanceRecord(
        instance_id="archive-0001",
        submission_id="test-miner",
        round_seed="test-seed",
        environment="archive",
        outcome=Outcome.SOLVED,
        spend=Decimal("0.25"),
        calls=(
            CallCost(
                model="test-model",
                provider="test-provider",
                quantization="fp16",
                input_tokens=250000,
                output_tokens=0,
                input_price_per_mtok=Decimal("1"),
                output_price_per_mtok=Decimal("0"),
            ),
        ),
        audited=False,
        audit_passed=None,
        reference_solved=True,
        reference_spend=Decimal("1"),
        transcript_digest="test-transcript",
        metadata={"test": True},
    )


def test_new_ledger_starts_at_genesis(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    header = make_header()

    ledger.open_round(header)

    assert ledger.tip(header.round_id) == GENESIS


def test_appended_record_produces_intact_hash_chain(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    header = make_header()

    ledger.open_round(header)
    chained = ledger.append(header.round_id, make_record())

    assert chained.previous == GENESIS
    assert len(chained.link) == 64
    assert ledger.verify(header.round_id) is True


def test_modified_record_breaks_hash_chain(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    header = make_header()

    ledger.open_round(header)
    ledger.append(header.round_id, make_record())

    records_path = tmp_path / header.round_id / "records.jsonl"

    payload = json.loads(records_path.read_text(encoding="utf-8"))
    payload["record"]["spend"] = "1.99"

    records_path.write_text(
        json.dumps(payload, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    assert ledger.verify(header.round_id) is False
