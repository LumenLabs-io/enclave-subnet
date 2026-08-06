# Enclave Subnet

A Bittensor subnet that pays agent runtimes for verified work per dollar.

Miners submit an agent as a container. Validators run each submission inside a partially observable environment where information must be purchased through interaction, meter every model call through a validator operated relay, and score the result as verified solutions per dollar. Success is verified against the environment's terminal state and computed programmatically, so no model sits in the scoring path and any third party can recompute a round from published evidence.

Miner emission follows a rank schedule over a rolling ledger of settled scores. When no submission qualifies, emission is burned.

## Why this subnet exists

An agent carries its history forever and pays for it on every step. Agentic workloads consume five to thirty times the tokens of a conversational exchange, and coding agents have been measured a thousandfold higher, almost entirely in input the model re reads rather than output it writes. Token prices fell through 2026 and budgets were still exceeded, because tokens per task grew faster than price per token fell.

Cheaper models do not solve that. Better use of models does, and two levers dominate: what an agent keeps in context, and which model answers each step. Neither is priced by any open competition today. Enclave prices both, together, in the only unit an operator actually pays.

## Why the environment is partially observable

A fully observable benchmark with a public generator can be solved by parsing rather than reasoning. Under a benchmark that scores accuracy alone this is a containable cheat. Under a score that includes cost it becomes the optimal strategy, because a program that resolves an instance without invoking a model has perfect accuracy at zero marginal cost, and any ratio of capability to spend is maximised by the one artifact that is worthless in production.

Enclave removes the attack surface instead of defending it. An answer that is not present in the observations cannot be recovered from them, and observations themselves are billed at published token prices, so exhaustive exploration costs real money and selective attention wins arithmetically.

Two consequences follow, and they are the design. A solver that never invokes a model is unable to participate rather than merely detected. And because reading is metered, an agent that discards something it will need buys it again, so memory quality is denominated in currency rather than assessed by a rubric.

## The score

For a round of `N` instances with a per instance spend cap `B`, published failure penalty `P`, and denominator floor `B_min`:

```text
gamma_i = max(x_i, B_min) + (1 - s_i) * P

Y = sum_i(s_i) / sum_i(gamma_i)
```

`s_i` is success, verified against the environment's terminal state by a grader outside the container. `x_i` is metered dollar spend from the round's signed price snapshot.

Capability is the numerator, never a factor weighted against cost. There is no exponent and no capability floor, because a lost solution removes a unit from the top and adds `P` to the bottom at the same time. Abstention is the most expensive strategy available: an agent attempting nothing scores zero, and one that solves a single cheap task and abandons the rest is charged the penalty on everything it skipped.

Full derivation, parameter choices, and how a score becomes a weight vector are in [docs/scoring.md](docs/scoring.md).

## Mining

You submit a container. It receives a task and a budget, reads the world through actions, calls models through the relay, and submits an answer. It has no network egress, never holds a provider credential, and never sees a price.

```sh
pip install enclave-subnet

enclave-miner contract                       # the protocol your container speaks
enclave-miner isolation <image>              # the sandbox it will run in
enclave-miner commit-image <hotkey> <image>  # commit before the deadline, reveal after
```

Your image must be digest pinned, reproducible from published source under a stated licence, and under the published size ceiling. Commit the digest on chain before the round's seed is fixed, which is what stops anyone building against the instances they will face.

Read [docs/miner-contract.md](docs/miner-contract.md) before writing anything. It is normative: it defines the four methods your container calls, what is stripped from your requests, the resource limits, and exactly how each failure mode scores. Nothing in it is discretionary on the validator's side.

The two things worth internalising before you optimise anything: observations are billed, so reading is a spending decision, and you choose which model answers each step from the round's priced catalogue, so routing is a scored choice rather than a detail the validator hides.

## Validating

You run the round loop. It fixes a seed after submissions close, generates instances, runs each submission in an isolated container against a metered relay you operate, grades against private construction state, folds records into scores, and publishes weights.

```sh
pip install "enclave-subnet[validator]"

cp .env.example .env                  # wallet, netuid, provider credential, price snapshot
enclave-validator preflight           # refuses to pass on a misconfiguration
enclave-validator open-round <id> <entropy> --opened-at <iso8601>
```

Entropy must be fixed only after submissions close. A block hash from the closing block is the intended source; supplying anything that existed earlier destroys the guarantee that instances are unpredictable.

Validators run on Linux, need Docker, and need a provider credential with headroom for the worst case round. Deployment, cadence, cost control, and how to read a round that looks wrong are in [docs/validator-guide.md](docs/validator-guide.md).

## Verification

Every weight the subnet sets is recomputable by anyone from published evidence. This is the property the whole design is arranged around, so it is worth exercising rather than trusting.

```sh
enclave-score verify <ledger> <round>          # walk the hash chain
enclave-score score  <ledger> <round> --json   # recompute yields and weights
```

A score is a pure function of the round seed, the price snapshot, and the transcript. No model in the score path, no evaluator held secret, no wall clock. If a recomputed weight differs from a published one, either the ledger was rewritten or the scoring code changed, and both are worth stopping for.

## Reference

| Document | Contents |
| --- | --- |
| [Miner contract](docs/miner-contract.md) | The normative interface a submission implements |
| [Validator guide](docs/validator-guide.md) | Running, operating, and verifying a validator |
| [Scoring](docs/scoring.md) | The yield score, parameter derivation, allocation |
| [Environments](docs/environments.md) | Families, generation, grading, metamorphic audit |
| [Architecture](docs/architecture.md) | Component map, round flow, trust boundaries |
| [Threat model](docs/threat-model.md) | Every attack, what closes it, residual risk |
| [Design principles](docs/design-principles.md) | Conventions this codebase holds to |
| [Whitepaper deltas](docs/whitepaper-deltas.md) | Deliberate departures from the whitepaper |

## Status

Pre alpha. The scoring core, metered relay, environment generation and grading, episode runner, sandbox, ledger, chain adapter, validator round loop, and miner SDK are implemented and covered by smoke tests. One environment family ships. Nothing here has been run against mainnet.

Known gaps are tracked in [docs/whitepaper-deltas.md](docs/whitepaper-deltas.md#open): CPU time is specified as priced but not yet metered, the published reference agent is not yet implemented, and originality screening is specified but not yet built.
