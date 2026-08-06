# Architecture

The component map, how a round flows through it, and where the trust boundaries sit.

## Components

| Module | Responsibility | Depends on |
| --- | --- | --- |
| `scoring` | Folds recorded evidence into a yield score and a weight vector | nothing |
| `environments` | Seeded generation, action handling, grading, audit transforms | `scoring` types |
| `relay` | Meters and prices every model call and every observation | `scoring` types |
| `sandbox` | Container isolation and lifecycle | nothing in the project |
| `episode` | Drives one submission on one instance, emits one record | `environments`, `relay`, `sandbox` |
| `ledger` | Append only hash chained records, round headers | `scoring` |
| `chain` | Metagraph reads, commitments, weight publication | nothing in the project |
| `validator` | The round loop: plan, evaluate, score, allocate, publish | everything |
| `miner_sdk` | What a submission imports to speak the protocol | `relay` protocol only |

`scoring` importing nothing is the property that matters. A third party recomputes every weight the subnet has ever set from published transcripts, without a socket, a container, or a chain connection.

## A round, end to end

```text
1  submissions close
2  entropy fixed          seed = H(round_id, entropy)          <- not before this point
3  plan_round             header + audit key + instance specs
4  ledger.open_round      header written once, digest fixed
5  for each submission:
     for each instance:
       build environment  (audited variant if the PRF selects it)
       start relay        unix socket, validator side
       run container      no network, read only, capabilities dropped
       grade              against private construction state
       append record      hash chained into the round ledger
6  score_submissions      fold records per submission
7  allocate_weights       rank to geometric schedule to ppm
8  publish                set_weights
9  reveal audit key       commitment opens after scoring
```

Step 2 is the ordering that makes instances unpredictable. The seed cannot exist before submissions are closed, so no submission can be built against the instances it will face. Step 9 is the ordering that makes audit selection unpredictable: the key is committed in the header at step 3 and revealed only after weights are set, so a container cannot compute which instances are watched.

## Trust boundaries

There are three, and each is drawn where an adversary would otherwise gain.

**The container boundary.** The submission is hostile code. It runs in a network namespace with no interface, a read only rootfs, all capabilities dropped, and no accelerator. Its only channel is a unix socket. Egress is structurally impossible rather than policy blocked, which is the difference between an attacker needing a kernel bug and an attacker needing a misconfiguration.

**The metering boundary.** The container reports nothing that is scored. It does not report its token usage, its cost, its success, or its termination. The relay counts tokens on the wire and prices them from the round's pinned snapshot; the grader computes success from the environment's private state; the runner observes termination. Where the container does report a number, as in the usage echoed back for its own budgeting, that number is for the submission's benefit and never reaches a score.

**The credential boundary.** The provider key lives in the relay. The container never holds it, never sees a price, and cannot name a provider. A submission that bypassed the relay would have no credential to bypass it with.

## Determinism

A score is a pure function of `(round seed, price snapshot, transcript)`.

Every source of nondeterminism is closed deliberately. Randomness comes from `SeededRandom` or a keyed PRF and is always passed in. Money is `Decimal` under an explicit precision context. Collections are sorted before they are folded. Weight vectors land on integers by largest remainder apportionment with ties broken by sorted identifier.

Two honest validators fold the same evidence into byte identical numbers. That is not a quality goal; Yuma consensus penalises divergence, so nondeterminism costs operators income.

## Where state lives

The ledger is the only durable state, and it is append only. Round headers are written once and refuse to be rewritten with a different digest. Records are hash chained, so a rewritten history fails `Ledger.verify` rather than passing silently.

Aggregates are never stored. A round score is a fold over records, recomputed on demand. If the fold changes, history recomputes rather than migrating, which is what allows a scoring fix to apply retroactively without a migration that could be disputed.

## What is deliberately absent

No model in the score path. A model in the evaluator destroys reproducibility and makes the evaluator a trusted party.

No secret holdout set. It has the same defect by another route: a score nobody else can recompute is a score nobody else can check.

No wall clock in scoring. Latency depends on evaluator hardware and provider load, neither of which a submission controls. Dollar cost is a published price multiplied by a metered count, reproducible on any machine, and it is the figure the operator is billed.
