from __future__ import annotations

from decimal import Decimal

from enclave.constants import WEIGHT_PPM_TOTAL
from enclave.scoring import (
    AllocationParams,
    InstanceRecord,
    Outcome,
    ScoringParams,
    allocate,
    charged_cost,
    score_round,
)


def record(instance: str, outcome: Outcome, spend: str) -> InstanceRecord:
    return InstanceRecord(
        instance_id=instance,
        submission_id="sub",
        round_seed="seed",
        environment="archive",
        outcome=outcome,
        spend=Decimal(spend),
    )


def test_failure_is_charged_spend_plus_penalty() -> None:
    params = ScoringParams()
    solved = charged_cost(record("a", Outcome.SOLVED, "0.40"), params)
    failed = charged_cost(record("b", Outcome.UNSOLVED, "0.40"), params)
    assert solved == Decimal("0.40")
    assert failed == Decimal("0.40") + params.failure_penalty


def test_denominator_floor_bounds_yield() -> None:
    params = ScoringParams()
    score = score_round("sub", [record("a", Outcome.SOLVED, "0")], params)
    assert score.yield_score <= params.max_yield


def test_infrastructure_faults_are_excluded_not_failed() -> None:
    score = score_round(
        "sub",
        [
            record("a", Outcome.SOLVED, "0.10"),
            record("b", Outcome.RELAY_UNAVAILABLE, "0.00"),
        ],
    )
    assert score.scored == 1
    assert score.excluded == 1


def test_failed_audit_voids_the_round() -> None:
    audited = InstanceRecord(
        instance_id="a",
        submission_id="sub",
        round_seed="seed",
        environment="archive",
        outcome=Outcome.UNSOLVED,
        spend=Decimal("0.10"),
        audited=True,
        audit_passed=False,
    )
    score = score_round("sub", [audited, record("b", Outcome.SOLVED, "0.10")])
    assert score.yield_score == Decimal(0)
    assert score.void_reason is not None


def test_allocation_sums_to_one_million_ppm() -> None:
    scores = [(f"sub-{i}", Decimal(20 - i)) for i in range(15)]
    allocation = allocate(scores, AllocationParams())
    assert sum(allocation.ppm.values()) == WEIGHT_PPM_TOTAL
    assert len(allocation.ppm) == AllocationParams().paid_ranks
