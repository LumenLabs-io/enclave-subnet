# Validator operations

Running a validator. What it needs, what it does each round, and what to check when a round looks wrong.

## Requirements

- Linux. The relay is a unix domain socket; there is no Windows path and there will not be one.
- Docker, with the validator's user able to reach the daemon.
- A provider credential with enough headroom for `submissions * instances * spend_cap` in the worst case.
- An accurate clock. Nothing in scoring reads the wall clock, but chain calls and provider calls both care.
- Outbound access to the chain endpoint and the provider. The sandbox itself needs none, by construction.

## Configuration

Every setting is `ENCLAVE_` prefixed and read from the environment or `.env`. Extra keys are rejected rather than ignored, so a typo fails at startup instead of silently taking a default.

```ini
ENCLAVE_NETUID=
ENCLAVE_WALLET_NAME=validator
ENCLAVE_WALLET_HOTKEY=default
ENCLAVE_CHAIN_ENDPOINT=wss://entrypoint-finney.opentensor.ai:443

ENCLAVE_LEDGER_ROOT=./state/ledger
ENCLAVE_SOCKET_ROOT=./state/sockets
ENCLAVE_PRICE_SNAPSHOT=./state/prices.json

ENCLAVE_FAMILIES=["archive"]
ENCLAVE_INSTANCES_PER_FAMILY=24
ENCLAVE_DEFAULT_MODEL=

ENCLAVE_PROVIDER_BASE_URL=
ENCLAVE_PROVIDER_API_KEY=

ENCLAVE_CONTAINER_CPUS=2.0
ENCLAVE_CONTAINER_MEMORY_GIB=4
ENCLAVE_CONTAINER_WALL_CLOCK_SECONDS=900
```

What an operator may change and what they may not is a hard line. Spend, logging, concurrency, and hardware are operator settings. The spend cap, the failure penalty, the denominator floor, the audit rate, and the price snapshot are part of the signed round header. An operator cannot change what a score means without it being visible on chain.

```sh
enclave-validator preflight
```

Preflight refuses to pass if the default model is not priced by the snapshot, if a configured family is unknown, or if the provider credential is missing outside dry run.

## The round

```sh
enclave-validator open-round round-042 "$ENTROPY" --opened-at 2026-01-01T00:00:00Z
```

`ENTROPY` must be fixed only after submissions close. A block hash from the closing block is the intended source. Supplying entropy that existed earlier destroys the unpredictability guarantee, because a miner could then have built against the instances.

Opening writes the header, fixes the seed, and stores the audit key beside the ledger. The header refuses to be rewritten with different contents, so re opening a round is safe and re opening it with different parameters is not possible.

Evaluation runs one container per instance with a fresh filesystem and no surviving volume. Cross instance amortisation is impossible rather than merely unrewarded, because work amortised across a batch would look like a genuine cost reduction to the validator while not reproducing for an operator running one task at a time.

After weights are set, reveal the audit key:

```sh
enclave-validator reveal-audit round-042
```

The command refuses to reveal a key that does not open the committed digest, which is the check that makes the commitment meaningful.

## Verification

Anyone can recompute a round from the published ledger. This is the property the whole design is arranged around, so it is worth running against your own rounds.

```sh
enclave-score verify ./state/ledger round-042
enclave-score score  ./state/ledger round-042 --json
```

`verify` walks the hash chain. `score` refolds every record into yields and a weight vector. Recomputed weights that differ from what was published mean either the ledger was rewritten or the scoring code changed, and both are worth stopping for.

## Reading a round that looks wrong

| Symptom | Where to look |
| --- | --- |
| Every submission scores zero | Grader or environment fault. Check `excluded` in the round score; a high count means `NO_DECISION` records, which are validator side. |
| One submission voided | A failed audit. `void_reason` names the instance. This is the mechanism working, not a fault. |
| Yields pinned at `1 / B_min` | Spend is at or below the denominator floor. Either the environment is too cheap to discriminate or metering is not counting something. |
| Yields far above peers | Check `crossed_relay` on that submission's records. An answer that never crossed the relay should already be a protocol violation. |
| Weights refused | `set_weights` returns rather than raises on some failures. The chain adapter interprets the return value; a rejection is surfaced as `WeightPublicationError` rather than logged as success. |
| Validator disagrees with peers | Nondeterminism. Compare `header.digest()` and the price snapshot digest first; a differing snapshot means the two validators priced differently. |

`excluded` deserves particular attention. It counts instances dropped as infrastructure faults, and a number that climbs is a validator problem being quietly absorbed rather than charged to submissions.

## Cadence

Scoring and weight setting are separate concerns and should not share a loop. A sweep across a full field can take hours; a weight cadence tied to it starves chain updates whenever the queue is busy. Weight publication should run on its own schedule, bounded below by the subnet's weight rate limit, and publish the most recent complete fold rather than waiting for the sweep in flight.

Weights are set from the rolling ledger, not from the round currently being evaluated. Building a vector from the in flight set zeroes a submission that was scored last round and is not being re run this round, which is a bug that is easy to introduce and hard to notice, because it looks like ordinary rank movement.

## Cost control

The worst case spend for a round is `submissions * instances * spend_cap`. That number should be computed before opening a round, not discovered afterwards.

Dry run mode substitutes a deterministic provider that makes no network call and costs nothing. It exercises the entire path, including metering and scoring, and is the correct way to validate a configuration change before spending real money on it.
