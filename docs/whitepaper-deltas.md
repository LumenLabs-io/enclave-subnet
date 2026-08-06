# Deltas from the whitepaper

Where this implementation departs from `enclave_whitepaper__4_.pdf`, and why. Each entry states what the whitepaper specifies, what the code does, and what changed the decision.

A delta is not a correction of the whitepaper's intent. Every one of these preserves the stated goal and changes the mechanism that reaches it.

## 1. The charged cost separates the cap from the price of failure

**Whitepaper.** `gamma_i = s_i * x_i + (1 - s_i) * B`. A single budget `B` serves as both the per instance spend cap and the amount charged for a failure.

**Implementation.** `gamma_i = max(x_i, B_min) + (1 - s_i) * P`, with `B` retained as the cap enforced at the relay.

**Why.** Under the original form, spend on a doomed instance is refunded in full: a failure costs `B` whether the agent spent nothing or spent the entire cap, so the marginal price of a dollar spent on a losing trajectory is zero. Retry until success then dominates honest single attempt play, slow failure strictly dominates fast failure, and a doomed trajectory becomes free reconnaissance for the rest of the round. The whitepaper asserts that a fixed budget makes brute force self defeating; under its own formula the arithmetic runs the other way. Full derivation in `docs/scoring.md`.

## 2. The denominator has a floor

**Whitepaper.** Not specified.

**Implementation.** `B_min` bounds charged cost below, and therefore bounds `Y` above by `1 / B_min`.

**Why.** `Y` is a ratio. One anomalously cheap solve, whether from a lucky instance, a transiently mispriced model, or an unmetered channel nobody has noticed, sends the score toward infinity and takes the whole pool under any proportional allocation. The floor is a structural guarantee rather than an estimate, which matters precisely because the failure it prevents comes from bugs not yet found.

## 3. Charged costs are winsorised before summing

**Whitepaper.** Not specified. `Y` is defined as a ratio of sums.

**Implementation.** Ratio of sums retained, with charged costs winsorised at the family's 95th percentile.

**Why.** Ratio of sums is the right aggregate because it is what an operator experiences, total work over total bill. Its weakness is influence: a few runaway trajectories dominate the denominator. Winsorising keeps the bill interpretation while bounding any single instance's contribution.

## 4. Weights are allocated by rank, not by score and not by a crown

**Whitepaper.** Does not specify an allocation rule. It describes displacing a leader through paired comparison with a derived margin, which reads as king of the hill.

**Implementation.** A geometric schedule over ranks, parameterised by `(paid_ranks, top_share)`, solved once and applied to whatever field appears.

**Why.** Proportional to score fails because `Y` is a mix dependent ratio. King of the hill fails for a sharper reason: the leading artifact is published, so a copy scores within noise of its original by construction and permanently sits inside any challenger margin. That hands the position to a copier on round to round variance and forces a champion re run on every near miss, which costs the copier nothing and the champion real money. Rank allocation dissolves both: a copy lands beside its original rather than displacing it.

## 5. A failed audit voids the round

**Whitepaper.** Specifies metamorphic auditing but not the granularity of the penalty.

**Implementation.** Any failed audit zeroes the entire round for that submission.

**Why.** The audit detects a categorical property. An agent answering a transformed instance with the untransformed answer has memorised rather than reasoned, and that is a property of the submission, not of the instance. Voiding categorically makes a 2 to 5 percent audit rate a total deterrent. Per instance zeroing would require auditing roughly half of every round to deter the same behaviour, which would cost more than it protects.

## 6. The submission selects a model from the priced catalogue

**Whitepaper.** Section 6 makes model choice the competitive surface and names step level routing as the specific opening the subnet exists to price.

**Prior draft of `docs/miner-contract.md`.** States that the relay selects the model and that a submission may not express a routing preference.

**Implementation.** The relay publishes the round's priced models in the `initialise` response. A submission may name any of them per call. Provider, quantisation, and routing hint fields are stripped rather than rejected, so the submission cannot reach past the catalogue to a specific endpoint or a cheaper quantisation of the same weights.

**Why.** These two positions cannot both hold. If the relay picks the model, routing is not contestable and half the stated thesis is unpriced; the subnet would then be scoring context management alone. Letting the submission choose from a catalogue the validator prices preserves the security properties that mattered, which are that the container never holds a credential, never sees a price, and cannot pin a specific provider endpoint, while making the routing decision a scored choice.

The cost gradient makes this safe without a rule. Choosing an underpowered model to save money loses solutions, and a lost solution removes a unit from the numerator and adds `P` to the denominator at the same time. There is no exchange rate at which systematically underspending pays.

**Status.** `docs/miner-contract.md` needs its `model.completions` section reconciled with this. Flagged rather than silently rewritten, because it is a mechanism decision rather than an editorial one.

## 7. Observations are billed as tokens

**Whitepaper.** States that every observation, tool call, and model invocation is metered, without specifying the observation price.

**Implementation.** The price snapshot must carry an `environment.observation` entry, and a snapshot without one is rejected at construction. Observation length is counted and charged at that price.

**Why.** Free observations collapse the construction. A scripted explorer would harvest every planted fact at zero cost, which restores full observability, and the memory thesis inverts: retaining a fact across `k` calls costs `k` times while re reading it costs once, so the score would reward forgetting. Making the entry mandatory rather than optional means a misconfigured snapshot fails loudly instead of quietly disabling the mechanism.

## 8. Infrastructure faults are excluded rather than failed

**Whitepaper.** Not specified.

**Implementation.** A relay outage, a provider error, an environment fault, or unreopenable evidence records a `NO_DECISION` verdict and is dropped from the fold. Every fault attributable to the submission still fails closed.

**Why.** Fail closed is correct for anything the submission controls, and every ambiguous outcome that it does control resolves against it. A validator side outage is not such an outcome. Charging it to the submission would make scores depend on validator health, which is both unfair and a divergence source between validators scoring the same round.

## 9. Paired records are the storage shape, not only a comparison method

**Whitepaper.** Describes paired comparison against comparators on identical instances.

**Implementation.** `PairedRecord` and the reference fields on `InstanceRecord` are fixed in the schema from the start, and `mcnemar_counts` reports all four concordance cells.

**Why.** Pairing cannot be retrofitted. Adding it later means re running every historical evaluation, so the record shape is fixed before anything is built on top of it. The whitepaper's claim of an order of magnitude reduction in instances assumes a between arm correlation near 0.9, which is optimistic for agents that differ meaningfully; a factor of 2.5 to 5 is the defensible figure and is what `docs/scoring.md` states.

## Open

- `docs/miner-contract.md` section on `model.completions` contradicts delta 6 and should be reconciled.
- CPU time is specified as priced in the miner contract but is not yet metered in `relay`. Until it is, a container performing genuine local inference on bundled weights would do real work at zero scored cost. The image size ceiling is the current mitigation and it is a weak one.
- The reference agent is defined in `docs/scoring.md` but not yet implemented, so `reference_multiple` has nothing to divide by and `Y_ref` is unpublished.
