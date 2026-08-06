from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from enclave.constants import DIGEST_DOMAIN_TRANSCRIPT
from enclave.environments.base import Environment, Grade, InstanceSpec
from enclave.relay.meter import Meter
from enclave.relay.pricing import PriceSnapshot
from enclave.relay.providers import Provider, TokenCounter
from enclave.relay.server import RelayServer, RelaySession
from enclave.sandbox.runner import ContainerOutcome, ContainerResult, Runner
from enclave.sandbox.spec import SandboxSpec
from enclave.scoring.records import InstanceRecord, Outcome

__all__ = ["EpisodeConfig", "EpisodeResult", "run_episode", "transcript_digest"]


@dataclass(frozen=True, slots=True)
class EpisodeConfig:
    spend_cap: Decimal
    default_model: str
    round_seed: str
    submission_id: str
    audited: bool = False


@dataclass(frozen=True, slots=True)
class EpisodeResult:
    record: InstanceRecord
    grade: Grade | None
    container: ContainerResult
    violations: tuple[str, ...]

    @property
    def solved(self) -> bool:
        return self.record.solved


def transcript_digest(
    spec: InstanceSpec,
    meter: Meter,
    answer: str | None,
    construction: str,
) -> str:
    payload: dict[str, Any] = {
        "instance": spec.canonical_json(),
        "construction": construction,
        "answer": answer,
        "calls": [
            {
                "model": c.model,
                "input_tokens": c.input_tokens,
                "output_tokens": c.output_tokens,
            }
            for c in meter.calls
        ],
        "spend": str(meter.spent),
    }
    body = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False
    )
    return hashlib.sha256(f"{DIGEST_DOMAIN_TRANSCRIPT}\0{body}".encode()).hexdigest()


def _classify(
    container: ContainerResult,
    session: RelaySession,
    meter: Meter,
    grade: Grade | None,
) -> Outcome:
    if container.outcome is ContainerOutcome.START_FAILED:
        return Outcome.ENVIRONMENT_FAULT
    if session.violations:
        return Outcome.PROTOCOL_VIOLATION
    if container.outcome is ContainerOutcome.TIMEOUT:
        return Outcome.TIMEOUT
    if meter.spent >= meter.spend_cap:
        return Outcome.CAP_EXHAUSTED
    if container.outcome is ContainerOutcome.CRASHED:
        return Outcome.CRASHED
    if session.answer is None:
        return Outcome.UNSOLVED
    if not meter.crossed_relay():
        return Outcome.PROTOCOL_VIOLATION
    if grade is None:
        return Outcome.UNSOLVED
    return Outcome.SOLVED if grade.solved else Outcome.UNSOLVED


async def run_episode(
    spec: InstanceSpec,
    environment: Environment,
    sandbox: SandboxSpec,
    runner: Runner,
    provider: Provider,
    counter: TokenCounter,
    snapshot: PriceSnapshot,
    config: EpisodeConfig,
    socket_dir: Path,
) -> EpisodeResult:
    meter = Meter(snapshot=snapshot, spend_cap=config.spend_cap)
    session = RelaySession(
        instance_id=spec.instance_id,
        seed=spec.seed,
        environment_name=spec.family,
        meter=meter,
        provider=provider,
        counter=counter,
        sink=environment,
        default_model=config.default_model,
    )

    server = RelayServer(session=session, socket_path=socket_dir / "relay.sock")
    await server.start()
    try:
        container = await runner.run(sandbox, f"enclave-{spec.instance_id}")
    finally:
        await server.stop()
        meter.seal()

    grade: Grade | None = None
    if session.answer is not None:
        try:
            grade = environment.grade(session.answer)
        except Exception:
            grade = None

    outcome = _classify(container, session, meter, grade)

    record = InstanceRecord(
        instance_id=spec.instance_id,
        submission_id=config.submission_id,
        round_seed=config.round_seed,
        environment=spec.family,
        outcome=outcome,
        spend=meter.spent,
        calls=meter.calls,
        audited=config.audited,
        audit_passed=(outcome is Outcome.SOLVED) if config.audited else None,
        transcript_digest=transcript_digest(
            spec, meter, session.answer, environment.construction_digest()
        ),
        metadata={
            "actions": session.action_count,
            "calls": meter.call_count,
            "container": container.outcome.value,
            "exit_code": container.exit_code,
            "grade_detail": grade.detail if grade is not None else "",
        },
    )

    return EpisodeResult(
        record=record,
        grade=grade,
        container=container,
        violations=tuple(session.violations),
    )
