from decimal import Decimal

from enclave.scoring.records import CallCost, InstanceRecord, Outcome, Verdict
from enclave.scoring.yield_score import ScoringParams, score_round


def make_record(
    *,
    outcome: Outcome,
    audited: bool = False,
    audit_passed: bool | None = None,
    spend: str = "0.25",
    instance_id: str = "archive-0001",
) -> InstanceRecord:
    return InstanceRecord(
        instance_id=instance_id,
        submission_id="test-miner",
        round_seed="test-seed",
        environment="archive",
        outcome=outcome,
        spend=Decimal(spend),
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
        audited=audited,
        audit_passed=audit_passed,
        reference_solved=True,
        reference_spend=Decimal("1"),
        transcript_digest="test-transcript",
        metadata={"test": True},
    )


def test_solved_record_produces_positive_yield() -> None:
    record = make_record(outcome=Outcome.SOLVED)

    result = score_round(
        "test-miner",
        [record],
        ScoringParams(
            spend_cap=Decimal("2.00"),
            failure_penalty=Decimal("0.25"),
            denominator_floor=Decimal("0.005"),
            winsorize_quantile=None,
        ),
    )

    assert result.verdict is Verdict.PASS
    assert result.solved == 1
    assert result.scored == 1
    assert result.total_charged == Decimal("0.25")
    assert result.yield_score == Decimal("4.000000000000")


def test_unsolved_record_applies_failure_penalty() -> None:
    record = make_record(outcome=Outcome.UNSOLVED)

    result = score_round(
        "test-miner",
        [record],
        ScoringParams(
            spend_cap=Decimal("2.00"),
            failure_penalty=Decimal("0.25"),
            denominator_floor=Decimal("0.005"),
            winsorize_quantile=None,
        ),
    )

    assert result.verdict is Verdict.PASS
    assert result.solved == 0
    assert result.scored == 1
    assert result.total_charged == Decimal("0.50")
    assert result.yield_score == Decimal("0")


def test_failed_metamorphic_audit_produces_fail_verdict() -> None:
    record = make_record(
        outcome=Outcome.SOLVED,
        audited=True,
        audit_passed=False,
    )

    result = score_round(
        "test-miner",
        [record],
        ScoringParams(
            spend_cap=Decimal("2.00"),
            failure_penalty=Decimal("0.25"),
            denominator_floor=Decimal("0.005"),
            winsorize_quantile=None,
        ),
    )

    assert result.verdict is Verdict.FAIL
    assert result.solved == 0
    assert result.scored == 1
    assert result.total_charged == Decimal("0")
    assert result.yield_score == Decimal("0")
    assert result.void_reason == (
        "metamorphic audit failed on instance archive-0001"
    )


def test_passed_audit_does_not_fail_submission() -> None:
    record = make_record(
        outcome=Outcome.SOLVED,
        audited=True,
        audit_passed=True,
    )

    result = score_round(
        "test-miner",
        [record],
        ScoringParams(
            spend_cap=Decimal("2.00"),
            failure_penalty=Decimal("0.25"),
            denominator_floor=Decimal("0.005"),
            winsorize_quantile=None,
        ),
    )

    assert result.verdict is Verdict.PASS
    assert result.solved == 1
    assert result.scored == 1
    assert result.total_charged == Decimal("0.25")
    assert result.yield_score == Decimal("4.000000000000")
