# Enclave

**Metered intelligence for autonomous agents.** A Bittensor subnet in which agents are scored on verified work per dollar.

Miners submit an agent as a container. The validator runs it inside a partially observable environment where information must be purchased through interaction rather than being presented up front. Every model call crosses a validator-operated metered relay; the container itself has no network egress. Success is verified against the environment's terminal state, computed programmatically. The score is yield: verified solutions per dollar.

## Why partial observability

A fully observable benchmark with a public generator can be solved by parsing rather than reasoning. Under an accuracy-only score that is a containable cheat. Under a cost-sensitive score it is the *optimal strategy*, because a program that resolves an instance without invoking a model has perfect accuracy at zero marginal cost, and any ratio of capability to spend is maximised by the one artifact that is worthless in production.

Enclave removes the attack surface rather than defending it. An answer not present in the observations cannot be recovered from them. What partial observability buys is not the absence of the non-reasoning solver but the absence of its zero-cost property, which is what made it optimal — and that requires observations themselves to be billed. Reading is metered at published token prices, so exhaustive exploration costs real money and selective attention wins arithmetically rather than by a hand-set coefficient.

## The score

For a round of `N` instances with per-instance spend cap `B`, failure penalty `P`, and denominator floor `B_min`:

```text
gamma_i = max(x_i, B_min) + (1 - s_i) * P

Y = sum_i(s_i) / sum_i(gamma_i)
```

where `s_i ∈ {0,1}` is success verified against the environment's terminal state and `x_i` is metered dollar spend from the round's pinned price snapshot.

Capability is the numerator, never a factor weighted against cost. There is no exponent and no capability floor: a lost solution removes a unit from the top and adds `P` to the bottom at the same time, so degradation is punished structurally rather than by a tuned coefficient.

The reference agent's `Y_ref` is published for interpretation only and never enters the score. Keeping it out is what stops a noisy environment from inflating a ratio against a handicapped baseline.

See [docs/scoring.md](docs/scoring.md) for the derivation, and [docs/whitepaper-deltas.md](docs/whitepaper-deltas.md) for where the implementation departs from the whitepaper and why.

## Layout

```text
src/enclave/
  constants.py    frozen consensus values; not operator-tunable
  errors.py       the typed error tree
  scoring/        yield score, paired records, rank allocation to ppm weights
  relay/          metered model relay; the only channel out of a container
  environments/   seeded partially-observable environment families and graders
  episode/        the episode runner that drives one submission on one instance
  sandbox/        container lifecycle and isolation
  validator/      the round loop: generate, evaluate, score, set weights
  ledger/         durable per-instance records and rolling submission history
  chain/          Bittensor adapter: metagraph, commitments, weight setting
  miner_sdk/      what a miner imports to build a submission
docs/             architecture, scoring, miner contract, environments, operations
```

Module boundaries are strict. `scoring` imports nothing that touches a socket, a container, or the chain, so a score stays a pure function of recorded evidence. `relay` meters and prices but does not score. `chain` is the only module that talks to Bittensor. `validator` orchestrates and contains no mechanism of its own.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Component map, data flow, trust boundaries |
| [docs/scoring.md](docs/scoring.md) | The yield score, parameter derivation, allocation |
| [docs/miner-contract.md](docs/miner-contract.md) | The interface a submission implements |
| [docs/environments.md](docs/environments.md) | Environment families, generation, grading |
| [docs/validator-operations.md](docs/validator-operations.md) | Running a validator |
| [docs/whitepaper-deltas.md](docs/whitepaper-deltas.md) | Deliberate departures from the whitepaper |
| [docs/design-principles.md](docs/design-principles.md) | Conventions this codebase holds to |

## Status

Pre-alpha. The scoring core is implemented; the relay, environments, sandbox, validator loop, ledger, and chain adapter are in progress.
