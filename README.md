# Enclave

**Enclave is a decentralised efficiency layer for autonomous agents.** It takes the two decisions that dominate what an agent consumes, what each model call carries and which model answers it, and produces a **Verified Agent Runtime (VAR)**: a portable, independently recomputable policy that completes the same verified work on a fraction of the tokens, backed by published evidence that the work was actually completed and the consumption actually measured.

```sh
pip install enclave-subnet
```

---

## Contents

- [Enclave](#enclave)
  - [Contents](#contents)
  - [What Enclave produces](#what-enclave-produces)
  - [What Enclave solves](#what-enclave-solves)
  - [How a Runtime is produced](#how-a-runtime-is-produced)
  - [Why environments are partially observable](#why-environments-are-partially-observable)
  - [The score](#the-score)
  - [Mining](#mining)
  - [Validating](#validating)
  - [Verification](#verification)
  - [Documentation](#documentation)
  - [Status](#status)

---

## What Enclave produces

A Verified Agent Runtime is a policy you run underneath your agent. It governs four decisions, continuously, that most agents make badly or not at all.

| Decision | What the Runtime does |
| --- | --- |
| **What to carry** | Assembles the working set for each call instead of resending the whole trajectory |
| **What to keep** | Retains what later steps will depend on and discards the rest |
| **What to reacquire** | Recovers a discarded detail on demand rather than carrying it defensively |
| **Which model** | Sends each step to the smallest model that can complete it correctly |

Three properties make it a Runtime rather than a benchmark result.

**Portable.** It governs context and routing rather than any one model's internals, so it survives a change of provider and does not decay when a new model ships.

**Recomputable.** Every Runtime carries the evidence of how it won. A score is a pure function of the round seed, the model schedule, and the transcript, so anyone can regenerate the round and confirm the Runtime completed the work it claims on the token consumption it claims. No model sits in the scoring path and no evaluator holds a secret.

**Private by separation.** The policy for managing memory is general, holds no user content, and is what the network publishes. The memory itself, your code, documents, and history, stays on your own infrastructure and never enters the network. Public knowledge of how to remember, private custody of what is remembered. That separation is the name.

---

## What Enclave solves

**Agents pay to re read their own history.** An agent re reads its accumulated context on every step, so a single observation is charged once per step it survives. Consumption grows with the product of context length and trajectory depth rather than with the work performed. Agentic workloads consume five to thirty times the tokens of a conversational exchange, and coding agents have been measured a thousandfold higher, almost entirely in input the model re reads rather than output it writes.

This does not resolve on its own. Per token prices fell sharply through 2026 and budgets were still exceeded, because tokens per task grew faster than price per token fell. Cheaper models do not fix it. Better use of models does.

**Every step is answered by the same model.** Query level routing has repeatedly matched a frontier model on a fraction of its consumption, and large scale evaluation reports a scaling effect in which a capable router surpasses every individual model in its pool, so routing is a capability rather than only an economy. Yet the intermediate calls inside an agent trajectory, the retrieval and analysis and debugging steps, are unrouted in every published benchmark. A file lookup and a patch that must be correct are answered by the same model at the same rate.

**Nobody can keep a fix current alone.** Both problems move. Models ship, workloads shift, and the right policy in one month is stale in the next. A runtime written once and frozen decays, which is why Enclave makes it a permanent contest rather than a product.

The two are solved together because they trade against each other. Carrying less context makes a smaller model sufficient more often, and a capable small model tolerates a leaner working set. Optimising either alone leaves the other's gains unclaimed.

---

## How a Runtime is produced

Independent teams submit candidate Runtimes. Each is run against environments none of them has seen, under a fixed token budget. The one that completes the most verified work per token consumed becomes the published Runtime, and holds that position only until something beats it.

```
submissions close
        │
        ▼
 entropy fixed          seed drawn from the closing block, never before
        │
        ▼
 instances generated    deterministic from (seed, family version)
        │
        ▼
 episodes run           each Runtime isolated, no egress, every call metered
        │
        ▼
 outcomes graded        environment terminal state, private construction state
        │
        ▼
 ledger folded          hash chained records
        │
        ▼
 weights published      recomputable by anyone
```

The ordering carries the guarantee. Submissions are committed before the seed exists, so no Runtime can be built against the instances it will face. Everything after the seed is deterministic, so anyone can regenerate a round and check the result.

---

## Why environments are partially observable

This is the central design decision, and it is what makes a Runtime's record mean anything.

A conventional benchmark generates a corpus, presents it, and asks questions about it. If the generator is public, which it must be for scores to be auditable, then a submission holding the generator holds its inverse. Every answer is a function of text the submission already possesses, so recovering it is parsing rather than reasoning. Obfuscation raises the cost of writing such a parser without removing the possibility, because the information required is by construction present.

Under a benchmark scoring accuracy alone this is a containable cheat. Under a score that accounts for consumption it becomes the winning strategy, because a program that resolves an instance without invoking a model has perfect accuracy at no consumption, and any ratio of work to tokens is maximised by the one artifact that is worthless in production.

Enclave removes the attack surface rather than defending it. Nothing is presented. Information exists only inside the environment and is obtained by acting on it, so an answer not present in the observations cannot be recovered from them, and a submission that never invokes a model is unable to participate rather than merely detected.

Two properties follow, and they are why the contest selects for a Runtime worth deploying:

- **Attention is a decision with consequences.** Observations are charged, so exhaustive exploration exhausts the budget and selective attention wins arithmetically rather than by a penalty coefficient.
- **Memory is load bearing rather than assessed.** A Runtime that discards something it later needs must reacquire it. Retention, salience, and retrieval stop being qualities judged by a rubric and become terms in the same measurement as everything else.

Families, generation, grading, and the metamorphic audit are specified in [docs/environments.md](docs/environments.md).

---

## The score

Consumption is measured in tokens, weighted by model tier from the round's published schedule so that a step answered by a large model is charged what it costs the network to answer it.

For a round of `N` instances with a per instance budget `B`, published failure penalty `P`, and denominator floor `B_min`:

```text
gamma_i = max(x_i, B_min) + (1 - s_i) * P

Y = sum_i(s_i) / sum_i(gamma_i)
```

`s_i` is success, verified against the environment's terminal state by a grader running outside the container. `x_i` is metered weighted token consumption from the round's signed schedule.

Work is the numerator, never a factor weighted against consumption. There is no exponent and no capability floor, because a lost solution removes a unit from the top and adds `P` to the bottom simultaneously. There is no exchange rate at which degradation pays.

Abstention is the most expensive strategy available. A Runtime attempting nothing scores zero. One that solves a single cheap instance and abandons the rest is charged the penalty on everything it skipped.

Derivation, parameter choices, and the mapping from score to weight vector are in [docs/scoring.md](docs/scoring.md).

---

## Mining

You submit a candidate Runtime as a container. It receives a task and a budget, reads the world through actions, calls models through the relay, and submits an answer. It has no network egress, never holds a provider credential, and never sees the schedule.

```sh
pip install enclave-subnet

enclave-miner contract                       # the protocol your container speaks
enclave-miner isolation <image>              # the sandbox it will run in
enclave-miner commit-image <hotkey> <image>  # commit before the deadline, reveal after
```

**Requirements.** Images must be digest pinned, reproducible from published source under a stated licence, and within the published size ceiling. Commit the digest on chain before the round's seed is fixed.

**Read [docs/miner-contract.md](docs/miner-contract.md) before writing anything.** It is normative. It defines the four methods your container implements, what is stripped from your requests, the resource limits, and how each failure mode scores. Nothing in it is discretionary on the validator's side.

**Two things to internalise before optimising anything.** Observations are charged, so reading is a decision rather than a free prelude to one. And you choose which model answers each step from the round's catalogue, so routing is a scored choice rather than a detail the validator hides from you.

---

## Validating

You run the round loop: fix a seed after submissions close, generate instances, run each submission in an isolated container against a relay you operate, grade against private construction state, fold records into scores, publish weights.

```sh
pip install "enclave-subnet[validator]"

cp .env.example .env                  # wallet, netuid, provider credential, model schedule
enclave-validator preflight           # refuses to pass on a misconfiguration
enclave-validator open-round <id> <entropy> --opened-at <iso8601>
```

> **Entropy must be fixed only after submissions close.** A block hash from the closing block is the intended source. Supplying anything that existed earlier destroys the guarantee that instances are unpredictable, which is the guarantee the rest of the design rests on.

**Requirements.** Linux, Docker, and a provider credential with headroom for a worst case round. Deployment, cadence, resource control, and how to read a round that looks wrong are in [docs/validator-guide.md](docs/validator-guide.md).

---

## Verification

Every weight the subnet sets is recomputable by anyone from published evidence. This is the property the design is arranged around, so exercise it rather than trusting it.

```sh
enclave-score verify <ledger> <round>          # walk the hash chain
enclave-score score  <ledger> <round> --json   # recompute yields and weights
```

A score is a pure function of the round seed, the model schedule, and the transcript. No model in the score path, no evaluator held secret, no wall clock. If a recomputed weight differs from a published one, either the ledger was rewritten or the scoring code changed. Both are worth stopping for.

---

## Documentation

| Document | Contents |
| --- | --- |
| [Miner guide](docs/miner-guide.md) | Build, test, and submit an agent, with a working example |
| [Miner contract](docs/miner-contract.md) | The normative interface a submission implements |
| [Validator guide](docs/validator-guide.md) | Installing, running, and verifying a validator |
| [Scoring](docs/scoring.md) | The yield score, parameter derivation, allocation |
| [Environments](docs/environments.md) | Families, generation, grading, metamorphic audit |
| [Architecture](docs/architecture.md) | Component map, round flow, trust boundaries |
| [Threat model](docs/threat-model.md) | Every attack, what closes it, residual risk |
| [Design principles](docs/design-principles.md) | Conventions this codebase holds to |
| [Whitepaper deltas](docs/whitepaper-deltas.md) | Deliberate departures from the whitepaper |

---

## Status

**Pre alpha.** Implemented and covered by smoke tests: scoring core, metered relay, environment generation and grading, episode runner, sandbox, ledger, chain adapter, validator round loop, miner SDK. One environment family ships. Nothing here has been run against mainnet.

Open items are tracked in [docs/whitepaper-deltas.md](docs/whitepaper-deltas.md#open): CPU time is specified as charged but not yet metered, the reference agent is not yet implemented, and originality screening is specified but not yet built.

Emission follows a rank schedule over a rolling ledger of settled scores. When no submission qualifies, emission is burned.