# Environments

An environment is a partially observable world with a private construction state. It presents a task and a set of actions; it does not present a corpus. Implemented in `src/enclave/environments/`.

## The design law

Ground truth derives from the environment's private construction state and must not be recoverable from the presented observations by any procedure cheaper than interacting with the environment.

Every other guarantee depends on this one. A benchmark that violates it is solvable by a program that costs nothing to run, and under a per dollar score that program wins outright rather than being a containable cheat.

Concretely, for a family to be admissible:

- The answer must not be a function of the task statement.
- The answer must not be derivable from the action schema.
- Reaching the answer must require observations, and observations must be billed.

The third clause is the one that is easy to miss. Partial observability alone removes the zero cost parser, but if reading were free a scripted explorer would harvest every planted fact for nothing and the environment would be fully observable with extra steps. Billing observations is what makes retention win where it should.

## The interface

```python
class Environment(Protocol):
    def briefing(self) -> str
    def actions(self) -> Sequence[ActionSpec]
    def act(self, action: str, arguments: Mapping[str, Any]) -> tuple[str, bool]
    def grade(self, answer: str) -> Grade
    def construction_digest(self) -> str
```

`construction_digest` commits to the private state. It is recorded in the transcript so a third party regenerating the instance from the published seed can confirm they built the same world before recomputing the score.

A family implements `generate(spec)` and `audited(spec, transform_seed)`. Both go through the same construction code; the audited variant differs only in the seed that derives fact values.

## The archive family

The first family. A sealed set of documents holding facts about a set of entities, reachable only through actions.

| Action | Returns | Cost |
| --- | --- | --- |
| `index` | Every document identifier and title | One short observation |
| `search(term)` | Identifiers of documents containing a term | One short observation |
| `read(doc_id)` | The full text of one document | One long observation |

Facts are scattered across documents and padded with filler that is indistinguishable in form. Recovering everything requires reading everything, and reading is billed at the observation price.

### Post hoc grading

The task asks for every attribute of every entity. Which pairs are actually graded is fixed by the generator but never revealed, and the briefing states plainly that the subset is chosen after the run completes.

This is what makes indistinguishability true by construction. At execution time no observation is identifiable as load bearing, so an agent cannot reason carefully where it is watched and cheaply everywhere else. It also prices memory correctly: since anything might be graded, discarding a fact is a bet, and the bet is settled in dollars when the fact has to be bought again.

### The audited variant

`audited()` rebuilds the same structure with fact values derived from a transform seed rather than the instance seed. Document count, entity names, filler, and layout are identical; only the values move, and they move deterministically through the same generator that produced the original.

An agent that memorised the original instance answers with stale values and fails. An agent that actually read the documents answers correctly. That is a covariance relation: it prices memorisation as wrong rather than merely unmatched.

Audited instances are indistinguishable from ordinary ones. Nothing in the briefing, the action schema, or the observations marks them.

## Audit selection

Selection is a keyed PRF, not a public hash.

```python
key.selects(round_seed, instance_id, rate)   # HMAC(secret, domain || seed || instance)
```

The secret is generated when the round is planned, committed as `sha256(domain || secret)` in the round header, and revealed only after weights are set.

Selecting by a public hash of the seed and instance identifier would be a serious error: a container could compute the audit set before acting and reason honestly on exactly those instances. Transform parameters are drawn from the same key for the same reason. A public covariance law lets a memoriser compute the transformed answer as easily as an honest agent, which inverts the mechanism it was built to enforce.

A failed audit voids the entire round rather than the audited instance. See `docs/scoring.md` for why that decouples audit rate from the failure penalty.

## Adding a family

1. Implement `Family` and `Environment` in `environments/families/`.
2. Register it in `environments/registry.py`.
3. Verify the design law holds. If an answer is recoverable from the briefing or the action schema, the family is inadmissible regardless of how interesting it is.
4. Confirm two generations from one seed produce identical `construction_digest`.
5. Confirm the audited variant defeats a memoriser: solving the original and replaying those answers against the audited instance must fail.

Steps 4 and 5 are covered by `tests/test_environments.py` and should be extended, not replaced, for each new family.

## Parameter derivation

Instance shape is a family parameter, not a global constant. What is global is the relationship between shape and the round's spend cap: `B` is derived from a stated multiple of the reference agent's 95th percentile cost on that family, so an environment that grows more expensive moves the cap with it rather than silently truncating honest trajectories.
