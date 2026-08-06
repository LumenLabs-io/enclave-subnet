# Scoring

Implemented in `src/enclave/scoring/`. Three modules, no cycles: `records` defines the evaluation unit, `yield_score` folds records into one submission's round score, `allocation` folds a field of scores into a weight vector.

## The score

For a round of `N` instances:

```
gamma_i = max(x_i, B_min) + (1 - s_i) * P

Y = sum_i(s_i) / sum_i(gamma_i)
```

| Symbol | Meaning |
| --- | --- |
| `s_i ∈ {0,1}` | Success, verified against the environment's terminal state by a grader outside the container |
| `x_i` | Metered dollar spend on instance `i`, from the round's signed price snapshot, capped at `B` |
| `B` | Per-instance spend cap, enforced at the relay |
| `P` | Published failure penalty, charged on any non-solve in addition to spend incurred |
| `B_min` | Denominator floor, bounding `Y` above by `1 / B_min` |

Capability is the numerator. There is no exponent and no capability floor, because a lost solution removes a unit from the top and adds `P` to the bottom simultaneously. The penalty for degradation is structural rather than tuned, which means there is no coefficient an operator can set wrongly and no exchange rate a miner can arbitrage.

## Why three parameters instead of one

The whitepaper uses a single per-task budget `B` as both the spend cap and the price of failure, with `gamma_i = s_i * x_i + (1 - s_i) * B`. That form has a specific defect: **spend on a doomed instance is refunded in full.** Whether the agent spent nothing or spent the entire cap, a failure is charged exactly `B`. The marginal price of a dollar spent on a losing trajectory is therefore zero, and three exploits follow from that single fact.

**Retry-until-success dominates.** An agent that repeatedly samples until it solves, stopping on the first success, pays only for the attempt that worked. Modelling `B/x = 10` and a per-attempt solve probability of `0.4`, the sequential retry strategy scores roughly 6.4× the honest single-attempt strategy. The whitepaper asserts that a fixed budget makes brute force self-defeating; under the original form the arithmetic says the reverse.

**Slow failure strictly dominates fast failure.** Since a failure costs `B` regardless, there is never a reason to stop early. The score actively punishes the stopping rule the design exists to elicit.

**A doomed trajectory becomes free reconnaissance.** Everything learned about the generator while burning a lost instance transfers to the other `N-1` instances in the round, at no charge.

Separating the two roles closes all three. `B` remains the cap; `P` becomes a published charge added *on top of* spend actually incurred. Spend on a failure is now real, so brute force costs what it costs and early abandonment is preferred where it is genuinely correct.

The two roles also want incompatible values, which is the deeper reason they cannot share a symbol. As a cap, `B` must sit well above the mean cost of a legitimate solve or it truncates honest trajectories. As a failure price, it must sit near that mean or the cost gradient dies. `P ≈ ` the reference agent's mean cost per solve keeps the capability-to-cost elasticity ratio near 2 rather than near 17.

## Why the denominator has a floor

`Y` is a ratio. Without a lower bound on charged cost, a single anomalously cheap solve, whether a lucky instance, a transiently mispriced model, or one unmetered channel, sends the score toward infinity, and under any proportional allocation that submission takes the entire pool.

`B_min` bounds `Y` above by `1 / B_min` unconditionally. It is a structural guarantee rather than an estimate, which matters because the failure mode it prevents is exactly the one that would follow from a metering bug nobody has noticed yet.

## Aggregation

`Y` is a **ratio of sums**, not a mean of per-instance ratios. The two differ by a factor of four or more on the same data when per-instance cost is heavy-tailed, and leaving the choice unstated is itself exploitable.

Ratio-of-sums is correct here because it is the quantity a customer experiences: total work completed divided by total bill. Mean-of-ratios corresponds to nothing anyone pays.

Its weakness is influence, because a handful of runaway trajectories can dominate the denominator. Charged costs are therefore **winsorised** at the family's 95th percentile before summing, which keeps the bill interpretation while bounding the contribution of any single instance.

## The reference agent

`Y_ref` is published so a score reads as a multiple of a known baseline. It never enters the score.

Keeping it out is not a stylistic choice. If capability were scored as a ratio against a reference, an environment that degrades the reference would hand every submission a free multiplier, and Enclave's own environments deliberately inject noise and withhold information, which degrades a naive reference by construction. A ratio-based capability gate would progressively stop binding as environments got harder, which is the opposite of the intent.

The baseline must be the best *shipped* agent configuration, not a naive full-context loop. Providers ship server-side compaction, tool-result clearing, and cached-read discounting; a multiple measured against no-optimisation proves nothing a provider changelog does not already prove.

## Metamorphic audit

An unpredictable fraction of instances is graded under a semantic transform whose expected result is recomputed through the same generator that produced the original. Invariance relations leave the answer unchanged; covariance relations change it deterministically, which prices memorisation as *wrong* rather than merely unmatched.

**A failed audit voids the entire round, not the audited instance.** The audit detects a categorical property. An agent answering a transformed instance with the untransformed answer has memorised rather than reasoned, and that is a property of the submission. Voiding categorically makes a 2–5% audit rate a total deterrent, which decouples audit design from the failure penalty entirely. Per-instance zeroing would require auditing roughly half of every round to deter the same behaviour.

Two implementation requirements follow, both in `docs/environments.md`:

- Audit selection is keyed by a PRF under an evaluator-held key, hash-committed before the round and revealed after scoring. Selecting by a public hash of the seed and instance id lets a container compute the audit set before acting and reason honestly on exactly those instances.
- Transform parameters are drawn from the same PRF. A public covariance law lets a memoriser compute the transformed answer as easily as an honest agent, which inverts the mechanism.

## Paired records

Every submission runs the identical instances as the reference agent, and records are stored per instance as `(instance_id, agent outcome, agent spend, reference outcome, reference spend)`.

Task difficulty variance is then common to both arms and cancels, which is the common-random-numbers technique from simulation. The gain is large: for binary outcomes with realistic between-arm correlation, pairing cuts the instances required for a given confidence by a factor of roughly 2.5 to 5. Claims of an order of magnitude assume a between-arm correlation near 0.9, which is optimistic for agents that differ meaningfully.

Pairing cannot be retrofitted. Adding it later means re-running every historical evaluation, which is why the record shape is fixed before anything else is built.

`mcnemar_counts` reports all four concordance cells. Only the discordant ones carry information about the difference between arms; if `both + neither` dominates, the environment is not discriminating and the round bought little regardless of how many instances it ran.

## Allocation

Scores become weights by **rank**, over a rolling ledger. Two alternatives were considered and rejected.

*Proportional to score* fails because `Y` is a ratio whose spread is mix-dependent. Bounding it above with `B_min` prevents divergence but does not make the top of the range meaningful, and one outlying round still captures a disproportionate share.

*King-of-the-hill with a decaying challenger margin* fails because the leading artifact is published. A copy scores within noise of its original by construction, so it permanently sits inside any margin. That hands the position to a copier on round-to-round variance, and forces a champion re-run on every near miss, a denial of service that costs the copier nothing.

Rank allocation dissolves both. There is no position to capture, a copy lands beside its original rather than displacing it, and the marginal entrant's expected value stays positive far enough down the field that entering is rational.

The curve is geometric, parameterised by `(paid_ranks, top_share)`:

```
paid_ranks = 12
top_share  = 0.25   ->  decay ≈ 0.759
schedule   = [0.250, 0.190, 0.144, 0.109, 0.083, 0.063, ...]
```

The decay ratio is solved **once** from those two parameters and then applied to whatever field actually appears. Re-solving per field size is the obvious implementation and it is wrong: at a field of size `1 / top_share` the requested top share *is* the uniform share, so the solve returns a flat schedule and every surviving miner is paid identically regardless of rank.

Ties share the mean of the schedule slots they jointly occupy. Near-identical scores are the expected state of a converged field, and a sort whose tie-break is incidental would hand real money to whichever identifier happens to compare first.

Total research purchased by the subnet is maximised by paying enough competitors that entry clears its cost, not by maximising the top prize. A schedule paying most of the pool to one winner suppresses the number of entrants and therefore the total effort the subnet buys, even though the headline prize is larger.

## Parameter derivation

`B`, `P`, and `B_min` are not preferences. They are part of the signed round header and each is derived from a measurement:

| Parameter | Derived from |
| --- | --- |
| `B` | A stated multiple of the reference agent's 95th-percentile cost per instance on that environment family |
| `P` | The reference agent's mean cost per solve on that family |
| `B_min` | The cheapest physically possible solve: one minimal call at the round's pinned prices |

Publishing the derivation rather than the number means the parameters move with the environment and the price sheet instead of requiring governance every time either changes.
