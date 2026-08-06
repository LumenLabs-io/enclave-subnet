# Threat model

The validator is the adversarial half of the protocol. A submission is hostile code written by someone paid to maximise a number, and every mechanism here exists because a specific attack was found against its absence.

Each entry states the attack concretely enough to implement, the property that closes it, and the module responsible.

This document is the acceptance criteria for the codebase. A change that weakens any property listed here requires an explicit design review rather than a local implementation shortcut.

## Trust boundaries

There are exactly three, and everything else follows from them.

| Boundary | Trusted side | Untrusted side |
| --- | --- | --- |
| The relay | Token counts, prices, model identity | Anything the container says it spent |
| The grader | Environment private construction state | Anything reachable from inside the container |
| The runner | Observed termination, wall time, exit status | Anything the submission announces about itself |

A value that crosses inward is an assertion to be checked. A value that never crosses is a fact.

## Cost and scoring

### Solve without invoking a model

An agent that resolves an instance by parsing or computation rather than inference has perfect accuracy at zero metered cost, which maximises any capability-per-dollar ratio. Under an accuracy-only benchmark this is a containable cheat; under a cost score it is the optimal strategy.

**Closed by** partial observability plus billed observations. Ground truth derives from the environment's private construction state and is not recoverable from presented observations by any procedure cheaper than interacting. Observations reach the container only as model-channel content, so reading is metered. **Modules:** `environments`, `relay`.

### Unbilled inference

Partial observability removes the parser's *source*; it does not remove its zero cost. A container carrying bundled model weights does genuine reasoning at zero metered spend.

**Closed by** model mediation as protocol law: every scored answer must be a token stream that crossed the relay, trace-visible. No accelerator device is mapped into the container namespace, container CPU is capped and priced at a published instance rate, and image size is bounded. **Modules:** `relay`, `sandbox`.

### Free environment interaction

If observations are free, a scripted explorer harvests every planted fact at zero cost and the partial-observability construction collapses to a fully observable benchmark with extra steps. This also inverts the memory thesis: retaining a fact across `k` calls costs `k` times, while re-reading it costs once, so the score would reward forgetting.

**Closed by** billing observation delivery through the model channel at published token prices. Retention wins only where `k * tokens_per_fact < tokens_per_observation`, and stating that inequality is what makes memory the scored quantity. **Modules:** `environments`, `relay`.

### Understated spend

A submission reports its own usage favourably.

**Closed by** metering at the relay from bytes on the wire, priced by the round's signed snapshot. The container never holds a provider credential and never sees a price. **Module:** `relay`.

### Cost triage on a refunded failure

Where failure forfeits a fixed budget regardless of spend, the marginal price of a dollar on a losing trajectory is zero. Retry-until-success then dominates, slow failure dominates fast failure, and a doomed instance becomes free reconnaissance transferable to the rest of the round.

**Closed by** `gamma_i = max(x_i, B_min) + (1 - s_i) * P`, where spend is always charged and the penalty is additive rather than substitutive. **Module:** `scoring`.

### Divergent score from an unmetered channel

Any channel not priced sends `Y` toward infinity and hands one submission the entire pool.

**Closed by** the denominator floor `B_min`, which bounds `Y` above by `1 / B_min` structurally rather than by estimate. This is a backstop against metering bugs not yet discovered, not a substitute for metering. **Module:** `scoring`.

### Heavy-tail capture of the denominator

Agentic cost is heavy-tailed; a few runaway trajectories otherwise dominate a ratio of sums.

**Closed by** winsorising charged cost at the family's 95th percentile before summing. **Module:** `scoring`.

## Environment and grading

### Grader tampering

The answer may be absent from observations, but the *completion predicate* is present and the agent must satisfy it. Deleting a failing test, patching the checker, or touching a graded artifact satisfies the predicate at zero relay cost. This is the most common failure of shipped terminal-state agent benchmarks.

**Closed by** grading outside the container against private construction state. Every object the grader reads is hashed at instance creation; mutation of grader-visible state scores `GRADER_TAMPERED`, which is a failure. The completion predicate is never a membership oracle the agent can query. **Modules:** `environments`, `episode`.

### Precomputed instances

Miners generate the round's instances in advance and cache solutions.

**Closed by** deriving instances from a seed fixed only after submissions close, published afterwards so anyone regenerates and verifies. **Modules:** `environments`, `chain`.

### Precompiled family policy

Sealing the seed closes instance precomputation and leaves policy precompilation untouched. A public generator *is* the transition and observation kernel; an adversary who never sees the seed still knows the state space, action grammar, observation function and completion predicate, and acting well in a known-model POMDP is belief-state filtering, which needs no language model.

**Closed by** billed observations, which bound the precompiled planner's advantage rather than removing it, plus environment families whose observation surface carries enough natural-language entropy to resist inversion. This is the residual risk in the design and it is stated rather than claimed closed. **Module:** `environments`.

### Anchor identification

If planted facts are distinguishable from filler, a retrieval heuristic preserves exactly them and discards the rest, at no reasoning cost.

**Closed by** post-hoc selection: many more candidate facts are planted than are queried, from a single generative process, and which are graded is chosen only after the trajectory completes. Indistinguishability is then true by construction rather than by assertion. **Module:** `environments`.

### Audit-set computation

Selecting audited instances by a public hash of the seed and instance id lets a container compute the audit set before acting and reason honestly on exactly those instances.

**Closed by** a PRF under an evaluator-held key, hash-committed before the round and revealed after scoring. Transform parameters are drawn from the same PRF, because a public covariance law lets a memoriser compute the transformed answer as easily as an honest agent. A base transform applies to every instance so there is no untransformed reference distribution for an offline discriminator to key on. **Module:** `environments`.

### Selective honesty under audit

An agent that detects it is being watched reasons properly only there.

**Closed by** audited instances being indistinguishable from ordinary ones, and by categorical voiding: a failed audit voids the round, not the instance. A 2–5% rate is then a total deterrent independent of the failure penalty. **Modules:** `environments`, `scoring`.

## Submission and identity

### Copying the leader

The leading artifact is published. A copy scores within noise of its original by construction, so under a challenger margin it captures the position on variance and forces a re-run on every near miss at no cost to itself.

**Closed by** rank allocation rather than king-of-the-hill, so there is no position to capture, plus multi-axis originality detection with first-seen-wins precedence: exact bytes, normalised source, structural fingerprint, and behavioural fingerprint on a fixed hidden probe battery. A later near-identical submission scores zero regardless of measured performance. **Modules:** `scoring`, `validator`.

### Sybil variance farming

A hard gate with zero downside is a free option: one policy submitted from many byte-different images sees some fraction clear by luck each round, and the losers cost nothing.

**Closed by** per-coldkey score aggregation rather than per-hotkey, a submission bond consumed by actual metered spend, and a per-coldkey rate limit of one submission per 24 hours. The continuous score removes the cliff that made the lottery profitable in the first place. **Modules:** `ledger`, `chain`.

### Validator-cost griefing

A submission that routes every step to the most expensive model imposes unbounded cost on whoever funds inference, at no cost to itself.

**Closed by** the miner funding their own inference through a relay the validator operates, plus a hard per-instance spend cap, plus a hard daily validator spend ceiling with a FIFO queue so insolvency is impossible by construction rather than by estimate. **Modules:** `relay`, `validator`.

### Nondeterminism griefing

A submission using wall-clock time, thread scheduling, or unseeded randomness produces different scores on two honest validators, forcing mutual disagreement and consensus clipping for the price of one registration.

**Closed by** supplying the seed at initialisation, requiring determinism as a protocol condition, and re-running one random instance per submission with rejection on divergence. **Modules:** `episode`, `sandbox`.

### Cross-instance amortisation

Work amortised across a batch does not reproduce for an operator running one task at a time, but shows up as a genuine cost reduction the validator cannot distinguish from a real one.

**Closed by** a fresh container and fresh tmpfs per instance, stated in the lifecycle contract so amortisation is a protocol violation rather than a strategy. **Module:** `sandbox`.

### Cache arbitrage between submissions

With provider-side prefix caching, a submission evaluated after another on the same instance inherits a warm cache and posts a lower cost for free.

**Closed by** pinning provider and quantisation, stripping routing-preference fields at the relay, and stating the cache convention in the round header so cost does not depend on evaluation order. **Module:** `relay`.

### Egress

Any network path out of the container defeats metering, isolation, and seed secrecy simultaneously.

**Closed by** a network namespace containing only loopback, meaning no interfaces, no resolver configuration, and no routes, with the sole channel out being a Unix domain socket to the relay. Egress is structurally impossible rather than policy-blocked. **Module:** `sandbox`.

## Residual risks

Stated because a threat model that claims completeness is not one.

**Precompiled planners on procedurally generated environments.** Procedurally generated environments are precisely the ones scripted solvers do well on; environments that resist scripting tend to be hand-authored. Billed observations bound the advantage without eliminating it. This is the primary open engineering problem and the component that cannot be forked.

**Terminal-state grading admits right-answer-wrong-reasoning.** Verifying the environment's end state says nothing about the path taken. An agent may reach a correct state through reasoning that would be unacceptable in production. Metamorphic auditing detects memorisation but not unsound reasoning that happens to generalise.

**Provider drift.** Prices, model availability, and backend routing change under us. Pinning a signed snapshot per round makes historical scores reproducible; it does not make them comparable across a provider deprecation.
