from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, localcontext

from enclave.constants import (
    DENOMINATOR_FLOOR,
    FAILURE_PENALTY,
    SCORING_PRECISION,
    SPEND_CAP,
    WINSORIZE_QUANTILE,
    YIELD_QUANTUM,
)
from enclave.errors import ScoringError
from enclave.scoring.records import InstanceRecord, PairedRecord, Verdict

__all__ = [
    "RoundScore",
    "ScoringParams",
    "charged_cost",
    "mcnemar_counts",
    "reference_multiple",
    "score_round",
    "settled_yield",
]


@dataclass(frozen=True, slots=True)
class ScoringParams:
    spend_cap: Decimal = SPEND_CAP
    failure_penalty: Decimal = FAILURE_PENALTY
    denominator_floor: Decimal = DENOMINATOR_FLOOR
    winsorize_quantile: Decimal | None = WINSORIZE_QUANTILE

    def __post_init__(self) -> None:
        if self.denominator_floor <= 0:
            raise ScoringError("denominator_floor must be positive; it bounds Y above")
        if self.failure_penalty < 0:
            raise ScoringError("failure_penalty must be non-negative")
        if self.spend_cap < self.denominator_floor:
            raise ScoringError("spend_cap must exceed denominator_floor")
        if self.winsorize_quantile is not None and not (
            Decimal("0.5") < self.winsorize_quantile <= Decimal(1)
        ):
            raise ScoringError("winsorize_quantile must lie in (0.5, 1]")

    @property
    def max_yield(self) -> Decimal:
        with localcontext() as ctx:
            ctx.prec = SCORING_PRECISION
            return Decimal(1) / self.denominator_floor


def charged_cost(record: InstanceRecord, params: ScoringParams) -> Decimal:
    spend = min(record.spend, params.spend_cap)
    charged = max(spend, params.denominator_floor)
    if not record.solved:
        charged += params.failure_penalty
    return charged


def _winsorize(values: Sequence[Decimal], quantile: Decimal) -> list[Decimal]:
    if not values:
        return []
    ordered = sorted(values)
    rank = int((quantile * len(ordered)).to_integral_value(ROUND_CEILING))
    ceiling = ordered[min(max(rank - 1, 0), len(ordered) - 1)]
    return [min(value, ceiling) for value in values]


@dataclass(frozen=True, slots=True)
class RoundScore:
    submission_id: str
    yield_score: Decimal
    solved: int
    scored: int
    excluded: int
    total_charged: Decimal
    verdict: Verdict
    void_reason: str | None = None

    @property
    def solve_rate(self) -> Decimal:
        if self.scored == 0:
            return Decimal(0)
        with localcontext() as ctx:
            ctx.prec = SCORING_PRECISION
            return Decimal(self.solved) / Decimal(self.scored)


def score_round(
    submission_id: str,
    records: Iterable[InstanceRecord],
    params: ScoringParams | None = None,
) -> RoundScore:
    params = params or ScoringParams()
    records = list(records)

    scoreable = [record for record in records if record.scoreable]
    excluded = len(records) - len(scoreable)

    if not scoreable:
        return RoundScore(
            submission_id=submission_id,
            yield_score=Decimal(0),
            solved=0,
            scored=0,
            excluded=excluded,
            total_charged=Decimal(0),
            verdict=Verdict.NO_DECISION,
            void_reason="no scoreable instances",
        )

    failed_audit = next(
        (r for r in scoreable if r.audited and r.audit_passed is False), None
    )
    if failed_audit is not None:
        return RoundScore(
            submission_id=submission_id,
            yield_score=Decimal(0),
            solved=0,
            scored=len(scoreable),
            excluded=excluded,
            total_charged=Decimal(0),
            verdict=Verdict.FAIL,
            void_reason=f"metamorphic audit failed on instance {failed_audit.instance_id}",
        )

    with localcontext() as ctx:
        ctx.prec = SCORING_PRECISION

        charged = [charged_cost(record, params) for record in scoreable]
        if params.winsorize_quantile is not None:
            charged = _winsorize(charged, params.winsorize_quantile)

        solved = sum(1 for record in scoreable if record.solved)
        total_charged = sum(charged, Decimal(0))
        value = (
            (Decimal(solved) / total_charged).quantize(YIELD_QUANTUM)
            if total_charged > 0
            else Decimal(0)
        )

    return RoundScore(
        submission_id=submission_id,
        yield_score=value,
        solved=solved,
        scored=len(scoreable),
        excluded=excluded,
        total_charged=total_charged,
        verdict=Verdict.PASS,
    )


def settled_yield(primary: RoundScore, reproduction: RoundScore) -> RoundScore:
    if primary.submission_id != reproduction.submission_id:
        raise ScoringError("cannot settle scores from different submissions")
    if primary.verdict is Verdict.NO_DECISION or reproduction.verdict is Verdict.NO_DECISION:
        return primary if primary.verdict is Verdict.NO_DECISION else reproduction
    if primary.verdict is Verdict.FAIL:
        return primary
    if reproduction.verdict is Verdict.FAIL:
        return reproduction
    return primary if primary.yield_score <= reproduction.yield_score else reproduction


def reference_multiple(agent: RoundScore, reference: RoundScore) -> Decimal | None:
    if reference.yield_score <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = SCORING_PRECISION
        return (agent.yield_score / reference.yield_score).quantize(YIELD_QUANTUM)


def mcnemar_counts(pairs: Sequence[PairedRecord]) -> dict[str, int]:
    counts = {"both": 0, "agent_only": 0, "reference_only": 0, "neither": 0}
    for pair in pairs:
        if pair.scoreable:
            counts[pair.concordance] += 1
    return counts
