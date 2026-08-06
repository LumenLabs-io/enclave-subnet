# Miner contract

What a submission is, how the validator drives it, and what it may and may not do. This document is normative: a submission that violates any MUST here scores as a failure, and where that failure is detectable as deliberate it scores as a protocol violation for the round.

Nothing in this contract is discretionary on the validator's side either. Every rule is enforced mechanically, because a rule enforced by operator judgement is a rule that differs between validators, and validators that disagree are penalised by consensus.

## What is submitted

An OCI container image. Its digest is committed on chain before the round's seed is fixed, under a commit-reveal so a miner cannot observe competitors' digests and copy before the deadline.

The image MUST be reproducible from published source under a stated licence. This is a scoring precondition, not an allowlist: any party can satisfy it, it is checked mechanically, and it exists so that originality screening has something to compare and so that a hostile binary cannot be scored blind.

The image MUST NOT exceed the published size ceiling. An image carrying a large precomputed index over the environment's asset space is doing at build time what the score exists to price at run time.

## The submission is an agent, not a middleware

The container receives a task and drives it to completion. It is not a memory layer plugged into a validator-supplied agent loop.

This is a deliberate choice with a real cost. A middleware contract would let the subnet claim it elicits a portable drop-in layer, but it would require the validator to author, freeze, version and publish an entire agent policy — system prompt, tool loop, retry semantics, stopping rule — whose behaviour would then be a scoring parameter as load-bearing as any published constant, and which no document currently specifies. Shipping the whole agent removes that component, makes cost end-to-end by construction, and reduces the contract to one endpoint.

The consequence is that a submission can win with a better prompt or a better control loop rather than better memory. That is acceptable: the metric is verified work per dollar, and a cheaper route to the same verified work is the thing being bought.

## Lifecycle

One container per instance. Fresh filesystem, fresh tmpfs, no volume surviving the instance.

This is stated as protocol rather than as an implementation detail because it makes cross-instance amortisation impossible rather than merely unrewarded. Work amortised across a batch does not reproduce for an operator running one task at a time, but would appear to the validator as a genuine cost reduction.

```
validator                                    container
    |                                             |
    |-- start (seed, instance_id, budget) ------->|
    |                                             |
    |<------------- POST /v1/model/completions ---|   metered, priced, logged
    |-- response --------------------------------->|
    |                                             |
    |<------------- POST /v1/env/act -------------|   observations billed as tokens
    |-- observation ------------------------------>|
    |                                             |
    |<------------- POST /v1/submit --------------|   terminal answer
    |                                             |
    |-- teardown -------------------------------->|
    |                                             |
  grade against private construction state
```

## Transport

Newline-delimited JSON-RPC over a Unix domain socket bind-mounted into the container at a published path.

The container runs in a network namespace containing only loopback: no interfaces, no resolver configuration, no routes. There is no TCP stack to escape into. Egress is structurally impossible rather than policy-blocked, which is the correct posture for code written by an adversary.

The container never holds a provider credential and never sees a price.

## Methods

### `initialise`

```
-> { "seed": str, "instance_id": str, "environment": str,
     "spend_cap": float, "deadline_seconds": int }
<- { "ready": true }
```

The seed is supplied rather than discovered. A submission MUST derive all of its randomness from it. Using wall-clock time, thread scheduling, process identifiers, or an unseeded generator is a protocol violation — not because nondeterminism is impolite, but because a submission whose behaviour varies between honest validators forces those validators to disagree, and consensus penalises them for it. One instance per submission per round is re-run and rejected on divergence.

### `model.completions`

```
-> { "messages": [...], "max_tokens": int, "stop": [...] }
<- { "content": str, "usage": { "input_tokens": int, "output_tokens": int } }
```

The relay selects the model. A submission MAY NOT name a model, a provider, a quantisation, or a routing preference; those fields are stripped rather than rejected, so a submission that sends them is not told whether they were honoured.

Usage is returned for the submission's own budgeting. It is not what is scored. Scoring uses the relay's own metering, from bytes on the wire, priced by the round's signed snapshot.

### `env.act`

```
-> { "action": str, "arguments": {...} }
<- { "observation": str, "terminal": bool }
```

Observations are delivered as model-channel content and billed at published token prices for their length.

This is the mechanism the whole construction rests on. If observations were free, a scripted explorer would harvest every planted fact at zero cost and the environment would be fully observable with extra steps. Worse, the memory thesis would invert: retaining a fact across `k` calls costs `k` times while re-reading it costs once, so the score would reward forgetting. Billing observations is what makes retention win where it should — when `k * tokens_per_fact < tokens_per_observation`.

### `submit`

```
-> { "answer": str }
<- { "accepted": true }
```

The answer MUST be carried by a token stream that crossed the relay. An answer the container computed without inference is not scored, because a submission that resolves an instance without invoking a model has perfect accuracy at zero metered cost and would maximise any capability-per-dollar ratio while being worthless in production.

## Resource limits

| Limit | Enforcement |
| --- | --- |
| Per-instance spend cap `B` | Relay refuses calls beyond it; instance terminates as `cap_exhausted` |
| Wall-clock deadline | Runner terminates; instance is `timeout` |
| CPU and memory | cgroup limits; CPU time priced at a published instance rate |
| Accelerator devices | None mapped into the container namespace |
| Filesystem | Read-only rootfs plus a tmpfs scratch, both discarded at teardown |

CPU is priced rather than merely capped because "reasoning" and "reasoning through the meter" are not the same thing. A container performing genuine inference on bundled weights would otherwise do real work at zero scored cost.

## Failure semantics

Every abnormal termination scores the instance as a failure **and** charges the spend incurred up to that point. There is no retry.

| Condition | Outcome |
| --- | --- |
| Wrong answer | `unsolved` |
| Spend cap reached | `cap_exhausted` |
| Deadline exceeded | `timeout` |
| Non-zero exit, panic, OOM | `crashed` |
| Malformed frame, model bypass, banned field | `protocol_violation` |
| Mutation of grader-visible state | `grader_tampered` |

These are recorded distinctly so an operator can diagnose a field. Scoring treats them identically, and must: any distinction between failure modes is an incentive to fail in whichever way looks cheaper.

## What the validator guarantees

So the contract is not one-sided:

- The seed is fixed only after the submission deadline, and published afterwards so any party regenerates the round and recomputes every score.
- The price snapshot is signed and pinned in the round header. Costs reprice identically on any machine at any later date.
- Provider and quantisation are pinned for the round, so a submission's cost does not depend on which submission ran before it.
- Scoring parameters `B`, `P`, `B_min` are in the signed round header, not operator preferences.
- Per-instance records are published, so a miner can verify their own score rather than trusting it.
- The development kit reproduces the evaluation locally: the same runner, a frozen instance set with cached reference outcomes and costs, a relay shim, and the scoring code. A miner's build-measure loop runs at their expense and their cadence, not the validator's.

## Identity and rate limits

Scores aggregate per **coldkey**, not per hotkey, and a submission carries a bond consumed by the metered spend its evaluation incurs.

One policy submitted from many byte-different images would otherwise be a free option on evaluation variance. Aggregating per coldkey and charging the evaluation to a bond makes the lottery cost real money.

One submission per coldkey per 24 hours.

## Originality

Submissions are screened on four axes: exact bytes, normalised source, structural fingerprint, and behavioural fingerprint on a fixed probe battery. First-seen wins by commitment time; a later near-identical submission scores zero regardless of its measured performance.

Screening is load-bearing rather than cosmetic, because the leading artifact is published. Without first-seen precedence a copy scores within noise of its original by construction and captures rank on variance alone.
