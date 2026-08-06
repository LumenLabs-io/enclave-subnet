from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from enclave.chain.client import ChainClient
from enclave.chain.payment import PaymentReader
from enclave.constants import MECHANISM_VERSION
from enclave.control.client import ControlPlane
from enclave.control.directive import Directive
from enclave.environments.prf import derive_seed
from enclave.errors import ConfigError, EnclaveError, ProtocolError
from enclave.ledger.store import Ledger
from enclave.relay.pricing import PriceSnapshot
from enclave.relay.providers import Provider, TokenCounter
from enclave.sandbox.runner import Runner
from enclave.sandbox.spec import Limits
from enclave.validator.discovery import admitted, discover, rejected, summary
from enclave.validator.round import (
    allocate_weights,
    evaluate_submission,
    plan_round,
    publish,
    score_submissions,
)
from enclave.validator.state import round_in_flight, round_lock

__all__ = ["DaemonConfig", "Validator", "run"]

log = logging.getLogger("enclave.validator")

_BACKOFF_SECONDS = 30.0
_MAX_BACKOFF_SECONDS = 600.0


@dataclass(frozen=True, slots=True)
class DaemonConfig:
    netuid: int
    default_model: str
    socket_root: Path
    state_root: Path
    round_interval_seconds: int = 7200
    weight_interval_seconds: int = 1200
    poll_interval_seconds: int = 60
    limits: Limits = field(default_factory=Limits)
    mechanism_version: int = MECHANISM_VERSION


@dataclass(slots=True)
class Validator:
    config: DaemonConfig
    control: ControlPlane
    chain: ChainClient
    ledger: Ledger
    runner: Runner
    provider: Provider
    counter: TokenCounter
    snapshot: PriceSnapshot
    payment_reader: PaymentReader | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _last_round_at: float = 0.0
    _directive: Directive | None = None

    def stop(self) -> None:
        self._stop.set()

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    def _refresh_directive(self) -> Directive:
        directive = self.control.directive()
        if self._directive is None or directive.revision != self._directive.revision:
            log.info(
                "directive revision %d in force (fee %d rao, cap %s, paused %s)",
                directive.revision,
                directive.upload_fee_rao,
                directive.spend_cap,
                directive.emissions_paused,
            )
        self._directive = directive
        return directive

    def _settled_round(self) -> str | None:
        rounds = [r for r in self.ledger.rounds() if self.ledger.records(r)]
        if not rounds:
            return None
        return sorted(rounds)[-1]

    async def _open_and_evaluate(self, directive: Directive) -> None:
        entropy = str(self.chain.metagraph().block)
        round_id = derive_seed(str(self.config.netuid), entropy, str(time.time()))[:16]

        candidates = discover(
            chain=self.chain,
            treasury=directive.treasury,
            upload_fee_rao=directive.upload_fee_rao,
            payment_reader=self.payment_reader,
        )
        submissions = admitted(candidates)
        log.info("round %s discovery: %s", round_id, summary(candidates))
        for hotkey, reason in rejected(candidates):
            log.info("  excluded %s: %s", hotkey[:12], reason)

        if not submissions:
            log.info("round %s has no admitted submissions; not opening", round_id)
            return

        plan = plan_round(
            round_id=round_id,
            entropy=entropy,
            opened_at=str(time.time()),
            snapshot=self.snapshot,
            families=directive.families,
            instances_per_family=directive.instances_per_family,
            spend_cap=directive.spend_cap,
        )
        self.ledger.open_round(plan.header)
        (self.ledger.root / round_id / "audit-key.hex").write_text(
            plan.audit_key.hex(), encoding="utf-8"
        )

        with round_lock(self.config.state_root, round_id, plan.header.opened_at):
            for submission in submissions:
                if self._stop.is_set():
                    log.info("stop requested; abandoning round %s", round_id)
                    return
                log.info(
                    "evaluating %s on %d instances",
                    submission.submission_id[:12],
                    len(plan.specs),
                )
                try:
                    await evaluate_submission(
                        plan=plan,
                        submission=submission,
                        ledger=self.ledger,
                        runner=self.runner,
                        provider=self.provider,
                        counter=self.counter,
                        snapshot=self.snapshot,
                        socket_root=self.config.socket_root,
                        default_model=self.config.default_model,
                        limits=self.config.limits,
                    )
                except EnclaveError:
                    log.exception("submission %s failed to evaluate", submission.submission_id[:12])
                with contextlib.suppress(EnclaveError):
                    self.control.heartbeat(round_id=round_id, note="evaluating")

        self._last_round_at = time.monotonic()
        log.info("round %s complete", round_id)

    async def score_loop(self) -> None:
        backoff = _BACKOFF_SECONDS
        while not self._stop.is_set():
            try:
                directive = self._refresh_directive()
                backoff = _BACKOFF_SECONDS

                if directive.emissions_paused:
                    log.info("emissions paused by the operator: %s", directive.pause_reason)
                elif time.monotonic() - self._last_round_at >= self.config.round_interval_seconds:
                    await self._open_and_evaluate(directive)

                with contextlib.suppress(EnclaveError):
                    self.control.heartbeat(note="idle")

            except ConfigError:
                log.exception("this validator is not permitted to run right now")
                await self._sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                continue
            except (ProtocolError, OSError):
                log.exception("control plane unreachable or malformed")
                await self._sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                continue

            await self._sleep(self.config.poll_interval_seconds)

    async def weight_loop(self) -> None:
        while not self._stop.is_set():
            await self._sleep(self.config.weight_interval_seconds)
            if self._stop.is_set():
                return
            try:
                directive = self._directive
                if directive is None:
                    continue
                if round_in_flight(self.config.state_root):
                    log.info("a round is in flight; holding the published weights")
                    continue

                settled = self._settled_round()
                if settled is None:
                    continue

                scores = score_submissions(self.ledger, settled)
                if not scores:
                    continue
                allocation = allocate_weights(scores)
                if not allocation.ppm:
                    log.info("no submission cleared the threshold in %s", settled)
                    continue

                uids, weights = publish(self.chain, allocation)
                log.info("published %d weights from round %s", len(uids), settled)
            except EnclaveError:
                log.exception("weight publication failed")

    async def run(self) -> None:
        log.info(
            "enclave validator starting: netuid %d, mechanism %d",
            self.config.netuid,
            self.config.mechanism_version,
        )
        await asyncio.gather(self.score_loop(), self.weight_loop())
        log.info("enclave validator stopped")


async def run(validator: Validator) -> None:
    await validator.run()
