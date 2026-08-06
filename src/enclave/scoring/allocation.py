from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

from enclave.constants import (
    PAID_RANKS,
    SCORING_PRECISION,
    TOP_SHARE,
    WEIGHT_PPM_TOTAL,
)
from enclave.errors import ScoringError

__all__ = [
    "Allocation",
    "AllocationParams",
    "allocate",
    "geometric_schedule",
    "largest_remainder_ppm",
    "solve_decay",
]


def solve_decay(paid_ranks: int, top_share: Decimal) -> Decimal:
    if paid_ranks == 1:
        return Decimal(1)

    with localcontext() as ctx:
        ctx.prec = SCORING_PRECISION

        def top_of(ratio: Decimal) -> Decimal:
            if abs(ratio - 1) < Decimal("1e-30"):
                return Decimal(1) / Decimal(paid_ranks)
            return (Decimal(1) - ratio) / (Decimal(1) - ratio**paid_ranks)

        low, high = Decimal("1e-12"), Decimal(1) - Decimal("1e-12")
        for _ in range(400):
            mid = (low + high) / 2
            if top_of(mid) > top_share:
                low = mid
            else:
                high = mid
        return (low + high) / 2


def geometric_schedule(length: int, decay: Decimal) -> list[Decimal]:
    if length <= 0:
        return []
    with localcontext() as ctx:
        ctx.prec = SCORING_PRECISION
        raw = [decay**i for i in range(length)]
        total = sum(raw, Decimal(0))
        return [weight / total for weight in raw]


def largest_remainder_ppm(shares: Sequence[tuple[str, Decimal]]) -> dict[str, int]:
    if not shares:
        return {}

    with localcontext() as ctx:
        ctx.prec = SCORING_PRECISION
        total = sum((share for _, share in shares), Decimal(0))
        if total <= 0:
            raise ScoringError("cannot apportion a non-positive total share")

        exact = [(key, share / total * WEIGHT_PPM_TOTAL) for key, share in shares]

    floors = {key: int(value) for key, value in exact}
    remainder = WEIGHT_PPM_TOTAL - sum(floors.values())

    ordered = sorted(exact, key=lambda item: (-(item[1] - int(item[1])), item[0]))
    for index in range(remainder):
        floors[ordered[index % len(ordered)][0]] += 1

    return floors


@dataclass(frozen=True, slots=True)
class AllocationParams:
    paid_ranks: int = PAID_RANKS
    top_share: Decimal = TOP_SHARE
    min_score: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.paid_ranks < 1:
            raise ScoringError("paid_ranks must be at least 1")
        if not 0 < self.top_share <= 1:
            raise ScoringError("top_share must lie in (0, 1]")
        if self.paid_ranks > 1 and self.top_share <= Decimal(1) / Decimal(self.paid_ranks):
            raise ScoringError(
                f"top_share {self.top_share} is at or below the uniform share for "
                f"{self.paid_ranks} ranks; no decreasing schedule satisfies it"
            )

    @property
    def decay(self) -> Decimal:
        return solve_decay(self.paid_ranks, self.top_share)


@dataclass(frozen=True, slots=True)
class Allocation:
    ppm: dict[str, int]
    schedule: list[Decimal]
    ranked: list[tuple[str, Decimal]]

    def __post_init__(self) -> None:
        if self.ppm and sum(self.ppm.values()) != WEIGHT_PPM_TOTAL:
            raise ScoringError(
                f"weights must sum to {WEIGHT_PPM_TOTAL} ppm, got {sum(self.ppm.values())}"
            )

    def share(self, submission_id: str) -> Decimal:
        with localcontext() as ctx:
            ctx.prec = SCORING_PRECISION
            return Decimal(self.ppm.get(submission_id, 0)) / Decimal(WEIGHT_PPM_TOTAL)

    def as_uid_ppm(self, uid_of: dict[str, int], n_uids: int) -> list[int]:
        vector = [0] * n_uids
        placed: list[tuple[str, Decimal]] = []
        for submission_id, ppm in self.ppm.items():
            uid = uid_of.get(submission_id)
            if uid is not None and 0 <= uid < n_uids:
                placed.append((submission_id, Decimal(ppm)))

        if not placed:
            return vector

        for submission_id, ppm in largest_remainder_ppm(placed).items():
            vector[uid_of[submission_id]] += ppm
        return vector


def allocate(
    scores: Sequence[tuple[str, Decimal]],
    params: AllocationParams | None = None,
) -> Allocation:
    params = params or AllocationParams()

    eligible = [(sid, score) for sid, score in scores if score > params.min_score]
    if not eligible:
        return Allocation(ppm={}, schedule=[], ranked=[])

    ranked = sorted(eligible, key=lambda item: (-item[1], item[0]))
    paid = ranked[: params.paid_ranks]
    schedule = geometric_schedule(len(paid), params.decay)

    shares: list[tuple[str, Decimal]] = []
    start = 0
    while start < len(paid):
        end = start
        while end + 1 < len(paid) and paid[end + 1][1] == paid[start][1]:
            end += 1
        with localcontext() as ctx:
            ctx.prec = SCORING_PRECISION
            shared = sum(schedule[start : end + 1], Decimal(0)) / Decimal(end - start + 1)
        for index in range(start, end + 1):
            shares.append((paid[index][0], shared))
        start = end + 1

    return Allocation(
        ppm=largest_remainder_ppm(shares),
        schedule=schedule,
        ranked=ranked,
    )
