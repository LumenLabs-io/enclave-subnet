from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from enclave.errors import IsolationError, LedgerError, PricingError
from enclave.ledger import Ledger, RoundHeader
from enclave.relay import (
    OBSERVATION_CHANNEL,
    Meter,
    ModelPrice,
    PriceSnapshot,
    SpendCapExceeded,
)
from enclave.sandbox import SandboxSpec
from enclave.scoring.records import InstanceRecord, Outcome


def snapshot() -> PriceSnapshot:
    return PriceSnapshot(
        snapshot_id="snap",
        taken_at="2026-01-01T00:00:00Z",
        entries=(
            ModelPrice("small", "p", "fp8", Decimal("0.10"), Decimal("0.40"), 128_000),
            ModelPrice(OBSERVATION_CHANNEL, "env", "none", Decimal("0.10"), Decimal(0), 10**6),
        ),
    )


def test_a_snapshot_must_price_the_observation_channel() -> None:
    with pytest.raises(PricingError):
        PriceSnapshot(
            snapshot_id="snap",
            taken_at="t",
            entries=(ModelPrice("small", "p", "fp8", Decimal("1"), Decimal("1"), 1000),),
        )


def test_observations_are_billed_like_tokens() -> None:
    meter = Meter(snapshot=snapshot(), spend_cap=Decimal("1.00"))
    charge = meter.charge_observation(10_000)
    assert charge.dollars > 0
    assert meter.observation_tokens() == 10_000


def test_the_spend_cap_seals_the_meter() -> None:
    meter = Meter(snapshot=snapshot(), spend_cap=Decimal("0.0001"))
    with pytest.raises(SpendCapExceeded):
        meter.charge_model("small", 1_000_000, 1_000_000)
    assert meter.exhausted


def test_an_image_must_be_pinned_by_digest(tmp_path: Path) -> None:
    with pytest.raises(IsolationError):
        SandboxSpec(image="agent:latest", socket_dir=tmp_path)


def test_the_sandbox_has_no_network_namespace(tmp_path: Path) -> None:
    spec = SandboxSpec(image="agent@sha256:" + "a" * 64, socket_dir=tmp_path)
    argv = spec.docker_argv("enclave-test")
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv


def header() -> RoundHeader:
    return RoundHeader(
        round_id="r1",
        seed="seed",
        opened_at="2026-01-01T00:00:00Z",
        price_snapshot_digest="d" * 64,
        audit_commitment="c" * 64,
        families=("archive",),
        instances_per_family=2,
    )


def test_the_ledger_detects_a_rewritten_record(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.open_round(header())
    for index in range(3):
        ledger.append(
            "r1",
            InstanceRecord(
                instance_id=f"archive-{index:04d}",
                submission_id="sub",
                round_seed="seed",
                environment="archive",
                outcome=Outcome.SOLVED,
                spend=Decimal("0.10"),
            ),
        )
    assert ledger.verify("r1")

    path = tmp_path / "r1" / "records.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[1])
    payload["record"]["spend"] = "0.00000001"
    lines[1] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert not Ledger(tmp_path).verify("r1")


def test_appending_to_an_unopened_round_is_refused(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    with pytest.raises(LedgerError):
        ledger.append(
            "missing",
            InstanceRecord(
                instance_id="a",
                submission_id="sub",
                round_seed="seed",
                environment="archive",
                outcome=Outcome.SOLVED,
                spend=Decimal("0.1"),
            ),
        )
