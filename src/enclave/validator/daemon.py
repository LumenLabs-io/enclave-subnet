from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from enclave.chain.client import ChainClient
from enclave.chain.payment import PaymentReader
from enclave.constants import (
    FALLBACK_RESERVED_HOTKEY,
    MECHANISM_VERSION,
    WEIGHT_PPM_TOTAL,
)
from enclave.control.client import ControlPlane
from enclave.control.directive import Directive
from enclave.environments.prf import derive_seed
from enclave.errors import (
    ChainError,
    ConfigError,
    EnclaveError,
    ProtocolError,
    WeightPublicationError,
)
from enclave.ledger.store import Ledger
from enclave.relay.providers import Provider, TokenCounter
from enclave.sandbox.runner import Runner
from enclave.sandbox.spec import Limits
from enclave.scoring.allocation import Allocation, apply_reserved_share
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


def _weight_summary(uids: Sequence[int], ppm: Sequence[int], limit: int = 5) -> str:
    pairs = sorted(zip(uids, ppm, strict=True), key=lambda pair: -pair[1])
    shown = ", ".join(f"uid {uid}={value / WEIGHT_PPM_TOTAL:.3f}" for uid, value in pairs[:limit])
    if len(pairs) > limit:
        shown += f", +{len(pairs) - limit} more"
    return shown


@dataclass(frozen=True, slots=True)
class DaemonConfig:
    netuid: int
    socket_root: Path
    state_root: Path
    round_interval_seconds: int = 7200
    weight_interval_seconds: int = 1200
    poll_interval_seconds: int = 60
    fallback_reserved_hotkey: str = FALLBACK_RESERVED_HOTKEY
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
    payment_reader: PaymentReader | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _last_round_at: float = 0.0
    # The newest revision the control plane has served, which may not be effective yet,
    # and the signed bytes it arrived as.
    _latest: Directive | None = None
    _pending_signed: tuple[dict[str, Any], str] | None = None
    # The revision currently governing scoring.
    _directive: Directive | None = None
    # The last control plane problem reported, so a standing condition is logged once
    # rather than every poll.
    _last_problem: str = ""
    _last_weight_problem: str = ""

    def stop(self) -> None:
        self._stop.set()

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    @property
    def _cache_path(self) -> Path:
        return self.config.state_root / "directive.json"

    def _write_cache(self, payload: Mapping[str, Any], signature: str) -> None:
        try:
            self.config.state_root.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps({"payload": dict(payload), "signature": signature}),
                encoding="utf-8",
            )
        except OSError:
            log.warning("could not cache the directive to %s", self._cache_path)

    def _read_cache(self) -> Directive | None:
        """The last directive that was in force here, re-verified on load.

        The cache is not a trust boundary. It is checked against the owner key exactly as
        a fetched directive is, so a tampered file fails the same signature check.
        """
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            directive = self.control.verify(raw.get("payload", {}), str(raw.get("signature", "")))
            directive.assert_runnable(self.config.mechanism_version, self.config.netuid)
        except EnclaveError:
            log.warning("the cached directive no longer verifies; ignoring it")
            return None
        return directive

    def _fetch_directive(self) -> Directive:
        directive, payload, signature = self.control.signed_directive()
        if self._latest is None or directive.revision != self._latest.revision:
            log.info(
                "directive revision %d served (fee %d rao, cap %s, paused %s, "
                "effective from block %d, prices %s)",
                directive.revision,
                directive.upload_fee_rao,
                directive.spend_cap,
                directive.emissions_paused,
                directive.effective_from_block,
                directive.price_snapshot.snapshot_id,
            )
        self._latest = directive
        self._pending_signed = (dict(payload), signature)
        return directive

    def _adopt(self, directive: Directive) -> Directive:
        if self._directive is None or self._directive.revision != directive.revision:
            log.info(
                "revision %d is now in force (prices %s, default model %s)",
                directive.revision,
                directive.price_snapshot.snapshot_id,
                directive.default_model,
            )
        self._directive = directive
        if self._pending_signed is not None and self._latest is directive:
            self._write_cache(*self._pending_signed)
        return directive

    def _in_force(self, block: int) -> Directive | None:
        """The revision that governs scoring at ``block``.

        A revision published ahead of its effective block is held back so that every
        validator adopts new prices on the same block rather than whenever its own poll
        happens to land.
        """
        candidate = self._latest
        if candidate is None:
            return None
        if candidate.effective_at(block):
            return self._adopt(candidate)

        previous = self._directive or self._read_cache()
        if previous is not None and previous.effective_at(block):
            log.info(
                "revision %d is not effective until block %d (chain at %d); "
                "scoring under revision %d until then",
                candidate.revision,
                candidate.effective_from_block,
                block,
                previous.revision,
            )
            return previous

        log.info(
            "revision %d is not effective until block %d (chain at %d) and no earlier "
            "revision is in force here; not opening a round",
            candidate.revision,
            candidate.effective_from_block,
            block,
        )
        return None

    def _settled_round(self) -> str | None:
        rounds = [r for r in self.ledger.rounds() if self.ledger.records(r)]
        if not rounds:
            return None
        return sorted(rounds)[-1]

    async def _open_and_evaluate(self) -> None:
        # One metagraph read serves both the entropy and the effective-block decision.
        block = self.chain.metagraph().block
        directive = self._in_force(block)
        if directive is None:
            return

        entropy = str(block)
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
            snapshot=directive.price_snapshot,
            families=directive.families,
            instances_per_family=directive.instances_per_family,
            default_model=directive.default_model,
            spend_cap=directive.spend_cap,
            failure_penalty=directive.failure_penalty,
            denominator_floor=directive.denominator_floor,
            audit_rate=directive.audit_rate,
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
                        socket_root=self.config.socket_root,
                        limits=self.config.limits,
                    )
                except Exception:
                    # One submission failing is that submission's problem. Aborting the
                    # round here would deny every other submission a score for it.
                    log.exception("submission %s failed to evaluate", submission.submission_id[:12])
                # A heartbeat is telemetry. It must never be able to abandon a round.
                with contextlib.suppress(EnclaveError, OSError, httpx.HTTPError):
                    self.control.heartbeat(round_id=round_id, note="evaluating")

        self._last_round_at = time.monotonic()
        log.info("round %s complete", round_id)

    async def score_loop(self) -> None:
        backoff = _BACKOFF_SECONDS
        while not self._stop.is_set():
            try:
                latest = self._fetch_directive()
                backoff = _BACKOFF_SECONDS
                self._last_problem = ""

                # The pause flag is read off the newest revision rather than the one in
                # force at a block: a kill switch that waits for a block is not one.
                if latest.emissions_paused:
                    log.info("emissions paused by the operator: %s", latest.pause_reason)
                elif time.monotonic() - self._last_round_at >= self.config.round_interval_seconds:
                    await self._open_and_evaluate()

                with contextlib.suppress(EnclaveError, OSError, httpx.HTTPError):
                    self.control.heartbeat(note="idle")

            except ConfigError:
                # Not admitted, below the required mechanism version, or serving the
                # wrong netuid. Recoverable only by an operator, so back off rather than
                # hammer the control plane.
                log.exception("this validator is not permitted to run right now")
                await self._sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                continue
            except ProtocolError as exc:
                # A control plane with nothing to serve yet is an expected state, not a
                # fault, so it gets one line rather than a traceback every poll.
                if "no directive has been published" in str(exc):
                    if self._last_problem != "no-directive":
                        self._last_problem = "no-directive"
                        log.info(
                            "no directive has been published yet; scoring is idle and the "
                            "whole weight vector goes to the reserved hotkey until there is one"
                        )
                else:
                    log.exception("control plane returned a response this build cannot use")
                await self._sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                continue
            except (ChainError, OSError, httpx.HTTPError):
                # httpx does not raise OSError, so the transport errors have to be named
                # explicitly; catching OSError alone would let a connection failure kill
                # the loop this handler exists to survive.
                log.exception("control plane or chain unreachable or malformed")
                await self._sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                continue
            except EnclaveError:
                log.exception("the scoring loop failed; retrying after backoff")
                await self._sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                continue
            except Exception:
                # A validator that exits on an unforeseen error stops earning and stops
                # being counted. Log it loudly and keep the loop alive.
                log.exception("unexpected error in the scoring loop; continuing")
                await self._sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                continue

            await self._sleep(self.config.poll_interval_seconds)

    def _settled_allocation(self) -> tuple[str | None, Allocation]:
        empty = Allocation(ppm={}, schedule=[], ranked=[])
        settled = self._settled_round()
        if settled is None:
            return None, empty
        scores = score_submissions(self.ledger, settled)
        if not scores:
            return settled, empty
        return settled, allocate_weights(scores)

    def _publish_unconfigured(self, view: Any) -> None:
        """Before the first directive exists, send the whole vector to the reserved hotkey.

        Only a validator that has never held a directive and has never settled a round
        takes this path. A cached revision or any scored round means the network is
        already running, and holding is then the correct response to a control plane
        that has gone quiet.
        """
        if self._read_cache() is not None or self._settled_round() is not None:
            return
        reserved = self.config.fallback_reserved_hotkey
        if reserved not in set(view.hotkeys()):
            log.error(
                "no directive is in force and the reserved hotkey %s is not registered "
                "on netuid %d; holding weights until one of those changes",
                reserved[:12],
                self.config.netuid,
            )
            return
        allocation = apply_reserved_share(
            Allocation(ppm={}, schedule=[], ranked=[]),
            reserved,
            Decimal(1),
        )
        log.info(
            "no directive in force; publishing the reserved vector to %s on netuid %d",
            reserved[:12],
            self.config.netuid,
        )
        uids, ppm = publish(self.chain, allocation, view)
        if not uids:
            log.error("no directive published and the reserved allocation resolved to no uid")
            return
        log.warning(
            "no directive published; weights set to the reserved hotkey %s | %s",
            reserved[:12],
            _weight_summary(uids, ppm),
        )

    def _note_weight_rejection(self, exc: WeightPublicationError) -> None:
        """Report a chain refusal as a line rather than a traceback.

        A rate limit is transient and worth seeing each time, because the message
        carries the retry window. Too little stake, or no permit, is a standing
        condition an operator has to resolve, so it is logged once until it changes.
        """
        detail = str(exc.__cause__ or exc)
        if "rate limit" in detail:
            self._last_weight_problem = "rate-limit"
            log.info("chain rate limit; holding until the next interval: %s", detail)
            return
        if "below the chain minimum" in detail or "below the floor" in detail:
            if self._last_weight_problem != "stake-floor":
                self._last_weight_problem = "stake-floor"
                log.error(
                    "this hotkey holds too little stake to set weights on netuid %d; "
                    "the chain refused the extrinsic. Stake it, or run from a hotkey "
                    "that already holds a validator permit",
                    self.config.netuid,
                )
            return
        log.exception("weight publication failed; holding until the next interval")

    async def weight_loop(self) -> None:
        # The first pass runs immediately. Waiting a full interval before the opening
        # publication leaves a restarted validator silent while the chain still holds
        # whatever it voted last, which reads as a stall rather than a cadence.
        first = True
        while not self._stop.is_set():
            if not first:
                await self._sleep(self.config.weight_interval_seconds)
            first = False
            if self._stop.is_set():
                return
            try:
                if round_in_flight(self.config.state_root):
                    log.info("a round is in flight; holding the published weights")
                    continue

                view = self.chain.metagraph()
                directive = self._in_force(view.block) if self._latest is not None else None
                if directive is None:
                    self._publish_unconfigured(view)
                    continue

                settled, earned = self._settled_allocation()
                allocation = apply_reserved_share(
                    earned, directive.reserved_hotkey, directive.reserved_share
                )
                if not allocation.ppm:
                    log.info("nothing has been earned and nothing is reserved; not publishing")
                    continue

                if directive.reserved_share > 0 and directive.reserved_hotkey not in set(
                    view.hotkeys()
                ):
                    log.error(
                        "reserved hotkey %s is not registered on netuid %d, so publishing now "
                        "would hand its share to the miners it is meant to withhold from; "
                        "holding weights until it is registered",
                        directive.reserved_hotkey,
                        self.config.netuid,
                    )
                    continue

                uids, ppm = publish(self.chain, allocation, view)
                log.info(
                    "weights set from round %s | %d uid(s): %s | reserving %s to %s",
                    settled or "no settled round",
                    len(uids),
                    _weight_summary(uids, ppm),
                    directive.reserved_share,
                    directive.reserved_hotkey[:12] or "nobody",
                )
            except WeightPublicationError as exc:
                self._note_weight_rejection(exc)
            except (EnclaveError, OSError, httpx.HTTPError):
                log.exception("weight publication failed; holding until the next interval")
            except Exception:
                log.exception("unexpected error in the weight loop; continuing")

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
