from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from enclave.constants import MONEY_QUANTUM
from enclave.errors import MeteringError
from enclave.relay.pricing import OBSERVATION_CHANNEL, ModelPrice, PriceSnapshot, price_tokens
from enclave.scoring.records import CallCost

__all__ = ["Charge", "Meter", "SpendCapExceeded"]


class SpendCapExceeded(MeteringError):
    def __init__(self, spent: Decimal, cap: Decimal) -> None:
        super().__init__(f"spend cap reached: {spent} of {cap}")
        self.spent = spent
        self.cap = cap


@dataclass(frozen=True, slots=True)
class Charge:
    cost: CallCost
    dollars: Decimal
    running_total: Decimal
    remaining: Decimal


def _as_call_cost(price: ModelPrice, input_tokens: int, output_tokens: int) -> CallCost:
    return CallCost(
        model=price.model,
        provider=price.provider,
        quantization=price.quantization,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_price_per_mtok=price.input_per_mtok,
        output_price_per_mtok=price.output_per_mtok,
    )


@dataclass(slots=True)
class Meter:
    snapshot: PriceSnapshot
    spend_cap: Decimal
    _calls: list[CallCost] = field(default_factory=list)
    _spent: Decimal = Decimal(0)
    _sealed: bool = False

    def __post_init__(self) -> None:
        if self.spend_cap <= 0:
            raise MeteringError("spend_cap must be positive")

    @property
    def spent(self) -> Decimal:
        return self._spent

    @property
    def remaining(self) -> Decimal:
        return max(self.spend_cap - self._spent, Decimal(0))

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    @property
    def calls(self) -> tuple[CallCost, ...]:
        return tuple(self._calls)

    @property
    def call_count(self) -> int:
        return len(self._calls)

    def seal(self) -> None:
        self._sealed = True

    def quote(self, model: str, input_tokens: int, output_tokens: int) -> Decimal:
        return price_tokens(self.snapshot.lookup(model), input_tokens, output_tokens)

    def affordable(self, model: str, input_tokens: int, projected_output: int) -> bool:
        return self.quote(model, input_tokens, projected_output) <= self.remaining

    def charge_model(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        reported_input: int | None = None,
        reported_output: int | None = None,
    ) -> Charge:
        billable_input = max(input_tokens, reported_input or 0)
        billable_output = max(output_tokens, reported_output or 0)
        return self._charge(self.snapshot.lookup(model), billable_input, billable_output)

    def charge_observation(self, tokens: int) -> Charge:
        return self._charge(self.snapshot.observation_price(), tokens, 0)

    def _charge(self, price: ModelPrice, input_tokens: int, output_tokens: int) -> Charge:
        if self._sealed:
            raise MeteringError("meter is sealed; the instance has terminated")
        if input_tokens < 0 or output_tokens < 0:
            raise MeteringError("token counts must be non-negative")

        dollars = price_tokens(price, input_tokens, output_tokens)
        cost = _as_call_cost(price, input_tokens, output_tokens)

        self._calls.append(cost)
        self._spent = (self._spent + dollars).quantize(MONEY_QUANTUM)

        if self._spent >= self.spend_cap:
            self._sealed = True
            raise SpendCapExceeded(self._spent, self.spend_cap)

        return Charge(
            cost=cost,
            dollars=dollars,
            running_total=self._spent,
            remaining=self.remaining,
        )

    def observation_tokens(self) -> int:
        return sum(c.input_tokens for c in self._calls if c.model == OBSERVATION_CHANNEL)

    def inference_tokens(self) -> tuple[int, int]:
        inputs = sum(c.input_tokens for c in self._calls if c.model != OBSERVATION_CHANNEL)
        outputs = sum(c.output_tokens for c in self._calls if c.model != OBSERVATION_CHANNEL)
        return inputs, outputs

    def crossed_relay(self) -> bool:
        return any(c.model != OBSERVATION_CHANNEL and c.output_tokens > 0 for c in self._calls)
