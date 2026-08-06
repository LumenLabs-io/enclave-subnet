# Miner guide

How to build, test, and submit an agent. This is the practical walkthrough. The normative rules live in [miner-contract.md](miner-contract.md), and a submission that disagrees with that document loses regardless of what this one says.

## You do not clone this repository

You build your own container. This repository is the subnet implementation, not a template you fork.

What you need from it is the SDK, which speaks the relay protocol so you do not have to implement newline delimited JSON-RPC yourself:

```sh
pip install git+https://github.com/LumenLabs-io/enclave-subnet
```

Your agent is your own code, your own repository, and your own image. The only contract is the four methods in [miner-contract.md](miner-contract.md).

## 1. Understand what you are being paid for

Two levers, and nothing else:

- **What you keep.** Observations are billed at published token prices. Reading a document costs money, and reading it twice costs twice. An agent that discards something it needs later buys it again.
- **Which model answers each step.** You choose from the round's priced catalogue on every call. A cheap model that solves the instance beats an expensive one that also solves it.

You are scored on verified solutions per dollar. A lost solution removes a unit from the numerator and adds the failure penalty to the denominator at the same time, so buying a solution is almost always worth it and wasting money on a lost cause is not.

## 2. Write the agent

```python
import json
from enclave.miner_sdk import EnclaveClient

with EnclaveClient() as client:
    brief = client.initialise()

    index = client.act("index")
    facts = {}
    for line in index.text.splitlines():
        doc_id = line.split("\t")[0]
        page = client.act("read", doc_id=doc_id)
        facts.update(parse(page.text))

    reply = client.complete(
        [{"role": "user", "content": summarise(facts)}],
        model=brief.models[0],
    )

    client.submit(json.dumps(assemble(facts, reply.content)))
```

`EnclaveClient` reads `ENCLAVE_SOCKET` from the environment, which the validator sets. `initialise` returns your instance id, seed, spend cap, wall clock deadline, and the priced model catalogue. Every call returns your remaining budget, so you can decide when to stop.

Three rules that are easy to violate by accident:

- **Derive all randomness from `brief.seed`.** Wall clock time, process ids, and unseeded generators make your agent score differently on two honest validators, which forces them to disagree and is a protocol violation.
- **Your answer must have crossed the relay.** An answer computed without inference scores as a protocol violation, because a solver that never invokes a model would have perfect accuracy at zero cost.
- **Do not expect the filesystem to persist.** One container per instance, fresh tmpfs, nothing survives.

## 3. Build the image

The container has no network egress at run time, so every dependency has to be baked in at build time.

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir git+https://github.com/LumenLabs-io/enclave-subnet
COPY agent.py /app/agent.py
USER 65534:65534
CMD ["python", "/app/agent.py"]
```

```sh
docker build -t ghcr.io/you/agent:v1 .
docker push ghcr.io/you/agent:v1
docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/you/agent:v1
```

That last command gives you the digest pinned reference, `ghcr.io/you/agent@sha256:...`, which is what you submit. A tag is not accepted, because a tag can be moved after it is committed.

Check what you are shipping into:

```sh
enclave-miner contract                    # the protocol and what gets stripped
enclave-miner isolation <image@sha256:…>  # the sandbox it runs in
```

Your image must also be reproducible from published source under a stated licence, and under the published size ceiling. An image carrying a precomputed index over the environment's asset space is doing at build time what the score exists to price at run time.

## 4. Commit before the deadline

```sh
enclave-miner commit-image <hotkey> <image@sha256:…>
```

This prints two things. Publish the commitment on chain before submissions close, and keep the reveal secret until the reveal window opens.

The order matters and it protects you. The round's seed is fixed only after submissions close, so nobody can build against the instances they will face. Commit and reveal means nobody can watch your digest and copy it before the deadline either.

## 5. Iterate

You cannot see the round's instances before they exist, but the mechanism is fully deterministic, so you can generate your own:

```python
from enclave.environments import build
from enclave.environments.registry import instance_spec

env = build(instance_spec("archive", "any-seed-you-like", 1))
print(env.briefing())
print(env.act("index", {})[0])
```

Same generator the validator uses. Different seed, same distribution. Build against it, measure your own token spend, and tune before you pay for a real round.

## What loses

| Mistake | Outcome |
| --- | --- |
| Reading every document when you only needed three | Solved, but a worse yield than an agent that read three |
| Retrying until something works | Spend is charged on failures too, so this is expensive rather than free |
| Answering without calling a model | `protocol_violation` |
| Naming a model outside the catalogue | `unpriced_model`, and the call does not happen |
| Running past the spend cap | `cap_exhausted`, and the instance is over |
| Memorising answers from a previous round | The metamorphic audit voids your entire round |

The last one is worth reading twice. An unpredictable fraction of instances is graded under a transform whose expected answer moves deterministically. Audited instances are indistinguishable from ordinary ones, and a failed audit voids the round rather than the instance.
