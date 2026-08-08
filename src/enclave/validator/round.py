from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from enclave.chain.client import ChainClient, MetagraphView
from enclave.constants import AUDIT_RATE, DENOMINATOR_FLOOR, FAILURE_PENALTY, SPEND_CAP
from enclave.environments.base import InstanceSpec
from enclave.environments.prf import AuditKey, derive_seed
from enclave.environments.registry import build, build_audited, instance_spec
from enclave.episode.runner import EpisodeConfig, EpisodeResult, run_episode
from enclave.errors import ProtocolError
from enclave.ledger.header import RoundHeader
from enclave.ledger.store import Ledger
from enclave.relay.pricing import PriceSnapshot
from enclave.relay.providers import Provider, TokenCounter
from enclave.sandbox.runner import Runner
from enclave.sandbox.spec import Limits, SandboxSpec
from enclave.scoring.allocation import Allocation, AllocationParams, allocate
from enclave.scoring.records import InstanceRecord
from enclave.scoring.yield_score import RoundScore, score_round

__all__ = [
    "RoundPlan",
    "Submission",
    "allocate_weights",
    "evaluate_submission",
    "plan_round",
    "publish",
    "score_submissions",
]


@dataclass(frozen=True, slots=True)
class Submission:
    submission_id: str
    image: str

    def __post_init__(self) -> None:
        if not self.submission_id:
            raise ProtocolError("a submission requires an identifier")


@dataclass(frozen=True, slots=True)
class RoundPlan:
    header: RoundHeader
    audit_key: AuditKey
    specs: tuple[InstanceSpec, ...]
    snapshot: PriceSnapshot
    default_model: str

    @property
    def round_id(self) -> str:
        return self.header.round_id

    def audited_ids(self) -> tuple[str, ...]:
        return tuple(
            spec.instance_id
            for spec in self.specs
            if self.audit_key.selects(
                self.header.seed, spec.instance_id, self.header.audit_rate
            )
        )


def plan_round(
    round_id: str,
    entropy: str,
    opened_at: str,
    snapshot: PriceSnapshot,
    families: Sequence[str],
    instances_per_family: int,
    default_model: str,
    audit_key: AuditKey | None = None,
    spend_cap: Decimal | None = None,
    failure_penalty: Decimal | None = None,
    denominator_floor: Decimal | None = None,
    audit_rate: Decimal | None = None,
) -> RoundPlan:
    if default_model not in snapshot.selectable:
        raise ProtocolError(
            f"default model {default_model!r} is not priced by snapshot {snapshot.snapshot_id}"
        )

    key = audit_key or AuditKey.generate()
    seed = derive_seed(round_id, entropy)

    header = RoundHeader(
        round_id=round_id,
        seed=seed,
        opened_at=opened_at,
        price_snapshot_digest=snapshot.digest(),
        audit_commitment=key.commitment(),
        families=tuple(families),
        instances_per_family=instances_per_family,
        spend_cap=SPEND_CAP if spend_cap is None else spend_cap,
        failure_penalty=FAILURE_PENALTY if failure_penalty is None else failure_penalty,
        denominator_floor=DENOMINATOR_FLOOR if denominator_floor is None else denominator_floor,
        audit_rate=AUDIT_RATE if audit_rate is None else audit_rate,
    )

    specs = tuple(
        instance_spec(family, seed, ordinal)
        for family in sorted(families)
        for ordinal in range(1, instances_per_family + 1)
    )
    return RoundPlan(
        header=header,
        audit_key=key,
        specs=specs,
        snapshot=snapshot,
        default_model=default_model,
    )


async def evaluate_submission(
    plan: RoundPlan,
    submission: Submission,
    ledger: Ledger,
    runner: Runner,
    provider: Provider,
    counter: TokenCounter,
    socket_root: Path,
    limits: Limits | None = None,
) -> tuple[EpisodeResult, ...]:
    # Prices and the default model come off the plan rather than from live configuration.
    # A directive revision landing mid round would otherwise fold this round's early
    # records under one schedule and its later records under another.
    header = plan.header
    snapshot = plan.snapshot
    results: list[EpisodeResult] = []

    for spec in plan.specs:
        audited = plan.audit_key.selects(header.seed, spec.instance_id, header.audit_rate)
        environment = (
            build_audited(spec, plan.audit_key, header.seed) if audited else build(spec)
        )

        socket_dir = socket_root / submission.submission_id / spec.instance_id
        socket_dir.mkdir(parents=True, exist_ok=True)

        sandbox = SandboxSpec(
            image=submission.image,
            socket_dir=socket_dir,
            limits=limits or Limits(),
        )

        result = await run_episode(
            spec=spec,
            environment=environment,
            sandbox=sandbox,
            runner=runner,
            provider=provider,
            counter=counter,
            snapshot=snapshot,
            config=EpisodeConfig(
                spend_cap=header.spend_cap,
                default_model=plan.default_model,
                round_seed=header.seed,
                submission_id=submission.submission_id,
                audited=audited,
            ),
            socket_dir=socket_dir,
        )
        ledger.append(header.round_id, result.record)
        results.append(result)

    return tuple(results)


def score_submissions(
    ledger: Ledger,
    round_id: str,
    submissions: Sequence[str] | None = None,
) -> dict[str, RoundScore]:
    header = ledger.header(round_id)
    params = header.scoring_params
    identifiers = tuple(submissions) if submissions else ledger.submissions(round_id)

    scored: dict[str, RoundScore] = {}
    for submission_id in identifiers:
        records: tuple[InstanceRecord, ...] = ledger.for_submission(round_id, submission_id)
        scored[submission_id] = score_round(submission_id, records, params)
    return scored


def allocate_weights(
    scores: dict[str, RoundScore],
    params: AllocationParams | None = None,
) -> Allocation:
    ranked = [
        (submission_id, score.yield_score)
        for submission_id, score in sorted(scores.items())
        if score.verdict.value == "pass"
    ]
    return allocate(ranked, params)


def publish(
    chain: ChainClient,
    allocation: Allocation,
    view: MetagraphView | None = None,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    graph = view or chain.metagraph()
    uid_of = {hotkey: index for index, hotkey in enumerate(graph.hotkeys())}
    for neuron in graph.neurons:
        uid_of[neuron.hotkey] = neuron.uid

    vector = allocation.as_uid_ppm(uid_of, graph.size)
    uids = tuple(uid for uid, weight in enumerate(vector) if weight > 0)
    weights = tuple(vector[uid] for uid in uids)
    if uids:
        chain.set_weights(uids, weights)
    return uids, weights
