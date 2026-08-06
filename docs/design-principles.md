# Design principles

Conventions this codebase holds to. They are listed because they are checkable: a reviewer should be able to point at a diff and say which principle it violates.

## Rationale lives in documentation, not in source

Source files carry no comments and no explanatory docstrings. Names, types, and structure carry the meaning; everything else belongs here in `docs/`.

This is not minimalism for its own sake. Comments drift out of step with the code beside them and are trusted anyway, which makes a stale comment worse than no comment. Design rationale also has a longer half-life than any particular implementation, and burying it in a file that will be rewritten guarantees it is lost. When a decision needs justifying, the justification goes in the document covering that component and the code stays readable.

The one exception is a message a user or operator will read: exception text, CLI help, log lines. Those are interface, not commentary, and they should be written carefully.

## Dependency direction is strictly inward

```text
scoring   <-  ledger   <-  validator
   ^            ^             |
   |            |             v
environment  episode  <-  sandbox, relay, chain
```

`scoring` depends on nothing in the project. It is a pure function of recorded evidence, which is what allows any third party to recompute every weight the subnet has ever set from published transcripts alone. Nothing that touches a socket, a container, or the chain may be imported into it.

`environments` depends only on `scoring` types. `episode` orchestrates. `validator` is the only module permitted to depend on everything, and it is the only module permitted to have a main loop.

Relative imports are banned outright (`ruff` enforces `ban-relative-imports = "all"`). Every import states the full path, so moving a module surfaces as a compile error rather than as a silently different resolution.

## Determinism is a correctness property, not a nicety

Two validators scoring the same transcript must produce identical numbers. Bittensor penalises validators whose weights diverge from consensus, so nondeterminism costs operators real income.

Concretely: no `dict`/`set` iteration order in anything that reaches a score, no unseeded randomness, no wall-clock reads in scoring paths, no floating-point summation whose order depends on collection order. Randomness is derived from an explicit seed or a keyed PRF and passed in, never taken from the ambient environment. Where a float comparison could flip a ranking, quantise before comparing.

A score is a pure function of `(round seed, price snapshot, transcript)`. If a change makes that untrue, the change is wrong.

## Records are append-only and canonically serialisable

Evaluation evidence is written once and never mutated. `InstanceRecord.canonical_json` is the single serialisation used for hashing, storage, and publication, so a digest computed anywhere matches a digest computed anywhere else.

Aggregates are always derived, never stored as the source of truth. A round score is a fold over records; if the fold changes, historical rounds recompute rather than being migrated.

## Nothing reported by a submission is trusted

The container is adversarial code. It does not report its own token usage, its own cost, its own success, or its own timing. Every one of those is measured at a boundary the submission does not control:

- **Cost** is metered at the relay, from bytes on the wire, priced by the round's signed snapshot.
- **Success** is computed by a grader reading the environment's private construction state, outside the container.
- **Termination** is observed by the runner, not announced by the submission.

Where a value must cross from the container, it is treated as an assertion to be checked, never as a fact.

## Fail closed

Every ambiguous outcome resolves against the submission. A crash, a timeout, an exhausted cap, a protocol violation, and a wrong answer all score identically as a failure that forfeits the penalty. They are recorded distinctly so operators can diagnose a field, but scoring must never distinguish them, because any distinction is an incentive to fail in whichever way looks cheaper.

Missing evidence is failure. A record that cannot be verified is not scored optimistically.

## Configuration is explicit, typed, and inert

Runtime configuration is a `pydantic-settings` model with no defaults that would silently change scoring. Anything affecting a score, meaning the spend cap, the failure penalty, the denominator floor, the price snapshot, and the audit rate, is part of the signed round header rather than an operator preference. An operator can change what their validator spends and how it logs; they cannot change what a score means without that being visible on chain.

## Errors are typed and narrow

No bare `except`. No exception swallowed without being recorded. Chain and network calls are the only places retries are permitted, and retry policy is explicit at the call site rather than buried in a decorator.

## Invariants are enforced in the type, not by convention

Verification lives outside this repository, so the code cannot rely on a test suite catching a violated invariant. Where an invariant can be made unrepresentable, it is.

`Allocation.__post_init__` rejects a weight vector that does not sum to exactly 1,000,000 ppm. `PairedRecord.__post_init__` rejects two records that do not share an instance and a round seed. `InstanceRecord.__post_init__` rejects negative spend and an audited record carrying no verdict. `ScoringParams.__post_init__` rejects a denominator floor of zero, which is the only thing standing between the score and divergence.

The rule is that a malformed object raises at construction rather than producing a wrong number downstream. An exception naming the violated invariant is worth more than a value nobody checks.

## Money and weights are exact

Every quantity that becomes a payout is `Decimal` under an explicit precision context, never `float`. Two validators must fold the same evidence into byte-identical numbers, and binary floating point does not reproduce across platforms, summation orders, or library versions.

Weight vectors land on integers by largest-remainder apportionment into ppm, with deterministic tie-breaking by sorted identifier. Float normalisation followed by rounding does not sum to the total and does not reproduce.
