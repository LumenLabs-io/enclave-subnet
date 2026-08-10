from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from enclave.constants import WEIGHT_PPM_TOTAL
from enclave.errors import ChainError, WeightPublicationError

__all__ = ["BittensorChain", "ChainClient", "MetagraphView", "Neuron", "OfflineChain"]


@dataclass(frozen=True, slots=True)
class Neuron:
    uid: int
    hotkey: str
    stake: float
    validator_permit: bool


@dataclass(frozen=True, slots=True)
class MetagraphView:
    netuid: int
    block: int
    neurons: tuple[Neuron, ...]

    @property
    def size(self) -> int:
        return len(self.neurons)

    def uid_of(self, hotkey: str) -> int | None:
        for neuron in self.neurons:
            if neuron.hotkey == hotkey:
                return neuron.uid
        return None

    def hotkeys(self) -> tuple[str, ...]:
        return tuple(neuron.hotkey for neuron in self.neurons)


@runtime_checkable
class ChainClient(Protocol):
    def metagraph(self) -> MetagraphView: ...

    def commitments(self) -> Mapping[str, str]: ...

    def publish_commitment(self, payload: str) -> None: ...

    def set_weights(self, uids: Sequence[int], weights_ppm: Sequence[int]) -> None: ...


def _validate_weights(uids: Sequence[int], weights_ppm: Sequence[int]) -> None:
    if len(uids) != len(weights_ppm):
        raise WeightPublicationError("uid and weight vectors differ in length")
    if not uids:
        raise WeightPublicationError("refusing to publish an empty weight vector")
    if any(w < 0 for w in weights_ppm):
        raise WeightPublicationError("weights must be non-negative")
    total = sum(weights_ppm)
    if total != WEIGHT_PPM_TOTAL:
        raise WeightPublicationError(
            f"weights must sum to {WEIGHT_PPM_TOTAL} ppm, got {total}"
        )


@dataclass(slots=True)
class OfflineChain:
    view: MetagraphView
    published: list[tuple[tuple[int, ...], tuple[int, ...]]] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    revealed: dict[str, str] = field(default_factory=dict)

    def metagraph(self) -> MetagraphView:
        return self.view

    def commitments(self) -> Mapping[str, str]:
        return dict(self.revealed)

    def publish_commitment(self, payload: str) -> None:
        self.commits.append(payload)

    def set_weights(self, uids: Sequence[int], weights_ppm: Sequence[int]) -> None:
        _validate_weights(uids, weights_ppm)
        self.published.append((tuple(uids), tuple(weights_ppm)))


def _stake_of(neuron: Any) -> float:
    for name in ("total_stake", "alpha_stake", "stake"):
        value = getattr(neuron, name, None)
        if value is None:
            continue
        try:
            return float(getattr(value, "tao", value))
        except (TypeError, ValueError):
            continue
    return 0.0


class BittensorChain:
    def __init__(self, netuid: int, wallet: Any, subtensor: Any) -> None:
        self._netuid = netuid
        self._wallet = wallet
        self._subtensor = subtensor

    def metagraph(self) -> MetagraphView:
        try:
            graph = self._subtensor.subnets.metagraph(self._netuid)
        except Exception as exc:
            raise ChainError(f"could not read metagraph for netuid {self._netuid}") from exc
        if graph is None:
            raise ChainError(f"netuid {self._netuid} was not found on chain")

        neurons = tuple(
            Neuron(
                uid=int(neuron.uid),
                hotkey=str(neuron.hotkey),
                stake=_stake_of(neuron),
                validator_permit=bool(getattr(neuron, "validator_permit", False)),
            )
            for neuron in graph.neurons
        )
        return MetagraphView(
            netuid=self._netuid, block=int(getattr(graph, "block", 0)), neurons=neurons
        )

    def commitments(self) -> Mapping[str, str]:
        try:
            raw = self._subtensor.subnets.commitments(self._netuid)
        except Exception as exc:
            raise ChainError("could not read commitments") from exc
        return {str(k): str(v) for k, v in dict(raw).items()}

    def publish_commitment(self, payload: str) -> None:
        try:
            self._subtensor.commit(wallet=self._wallet, netuid=self._netuid, data=payload)
        except Exception as exc:
            raise ChainError("commitment publication failed") from exc

    def set_weights(self, uids: Sequence[int], weights_ppm: Sequence[int]) -> None:
        import bittensor

        _validate_weights(uids, weights_ppm)
        uid_weights = {
            int(uid): value / WEIGHT_PPM_TOTAL
            for uid, value in zip(uids, weights_ppm, strict=True)
        }
        try:
            outcome = bittensor.set_weights(
                self._netuid,
                uid_weights,
                wallet=self._wallet,
                network=getattr(self._subtensor, "network", "finney"),
            )
        except Exception as exc:
            raise WeightPublicationError("set_weights raised") from exc

        success, message = _interpret(outcome)
        if not success:
            raise WeightPublicationError(f"set_weights rejected: {message}")


def _interpret(outcome: Any) -> tuple[bool, str]:
    if isinstance(outcome, tuple) and len(outcome) == 2:
        return bool(outcome[0]), str(outcome[1])
    if isinstance(outcome, bool):
        return outcome, ""
    return True, ""
