# Validator operations

Running a validator. What it needs, what it does each round, and what to check when a round looks wrong.

## Requirements

- Linux. The relay is a unix domain socket; there is no Windows path and there will not be one.
- Docker, with the validator's user able to reach the daemon.
- Python 3.11 or newer.
- A hotkey registered on netuid 92, **and admitted by the subnet owner**. Admission is out of band: the control plane answers `403` to a hotkey that is not on the list, and the validator will not score without a directive.
- No provider credential. Inference is paid for by the submission that uses it, and the relay meters what crosses it, so a validator never holds a provider key and never carries the field's inference cost.
- An accurate clock. Nothing in scoring reads the wall clock, but chain calls and provider calls both care.
- Outbound access to the chain endpoint, the control plane, and the provider. The sandbox itself needs none, by construction.

## Install

Clone the repository. Unlike a miner, who only needs the SDK and builds their own image, a validator runs this codebase as a service and needs the configuration template and the environment generators that ship with it.

```sh
git clone https://github.com/LumenLabs-io/enclave-subnet
cd enclave-subnet

python -m venv .venv && . .venv/bin/activate
pip install -e ".[validator]"
```

Installing in editable mode means `git pull` is the whole upgrade path. Every validator must run the same mechanism code, because two validators folding the same evidence into different numbers is a consensus divergence that costs both of them income.

Verify the install before configuring anything:

```sh
enclave-validator --help
enclave-score --help
```

## Configuration

Every setting is `ENCLAVE_` prefixed and read from the environment or `.env`.

```sh
cp .env.example .env
```

```ini
ENCLAVE_WALLET_NAME=validator
ENCLAVE_WALLET_HOTKEY=default

ENCLAVE_LEDGER_ROOT=./state/ledger
ENCLAVE_SOCKET_ROOT=./state/sockets

ENCLAVE_CONTAINER_CPUS=2.0
ENCLAVE_CONTAINER_MEMORY_GIB=4
ENCLAVE_CONTAINER_WALL_CLOCK_SECONDS=900
```

That is the whole file. What is *not* in it matters more than what is.

**Network identity is compiled in, not configured.** The control plane is `https://api.nclv.io` and the key whose signature every directive must carry is `5F4pTG5AzJwVoRUw97qPCVYAXUPthKTRoKLtKCsVbpccVKkg`. Neither is a setting. A validator that could point itself at another control plane, or trust another signing key, could sign itself a directive granting whatever prices and scoring parameters it liked and still call itself an Enclave validator. Setting `ENCLAVE_CONTROL_PLANE_URL` or `ENCLAVE_OWNER_PUBLIC_KEY` to anything other than the compiled value is refused at startup with a message saying so; setting it to the correct value is accepted and ignored, so an older `.env` still parses. Changing them for real means shipping a fork, which is visible and which diverges from consensus rather than quietly redefining it.

**There is no price snapshot to author.** The model schedule, the default model, the enabled families, the instance count, the spend cap, the failure penalty, the denominator floor, and the audit rate all arrive in the owner signed directive, and the round header records the digest of the snapshot that priced the round. A validator cannot price a round differently from its peers by misconfiguring itself. That is the point: prices are the denominator of every yield, so two validators pricing differently is a consensus divergence that costs both of them income, and recording a digest only makes that detectable afterwards rather than impossible.

Spend, logging, hardware, and paths remain operator settings, because none of them change a number the network agrees on.

Extra keys are rejected when they appear in the `.env` file, so a typo there fails at startup. Note that the same typo passed as an environment variable is silently ignored instead — that is `pydantic-settings` behaviour, and it means the Docker path below, which injects configuration through the environment, does not get this protection.

Every expected failure is a message and an exit code rather than a traceback: an unadmitted hotkey, an unreachable control plane, a directive this build cannot use, a missing wallet, a malformed `.env`.

```sh
enclave-validator preflight
```

Preflight signs a request with your hotkey, fetches the directive, verifies the owner signature, and prints the parameters the round will actually run under, including the worst case spend. It fails if this hotkey is not admitted, if the signature does not verify, or if the directive enables an environment family this build cannot generate. Because it exercises the real admission path, a passing preflight means the daemon will start.

## Running it

```sh
enclave-validator run
```

That is the whole operation. The daemon runs two independent loops.

The **scoring loop** fetches the signed directive, verifies the owner signature itself, and refuses to continue if this validator is not admitted or is below the network's `min_mechanism_version`. When a round is due it discovers submissions from on chain commitments, checks each reveal against its commitment and its payment against the chain, opens a round, and evaluates every admitted submission while holding the round lock.

The prices a round is scored against are read off the directive once, when the round is planned, and carried on the plan for the life of the round. A revision landing mid sweep therefore cannot fold a round's early records under one schedule and its later records under another.

### When a new revision takes effect

A directive carries `effective_from_block`. A revision published ahead of its block is held until the chain reaches it, so every validator adopts new prices on the same block rather than whenever its own poll happens to land. Until then the previous revision keeps governing. The last directive that was in force is cached at `state/directive.json` and re-verified against the owner key on load, so a brief control plane outage does not strand a validator without parameters; the cache is not a trust boundary, and a tampered file fails the same signature check a fetched one does.

`emissions_paused` is the exception: it is honoured from the newest revision immediately, without waiting for a block. A kill switch that waits is not a kill switch.

The **weight loop** runs on its own schedule. This separation is deliberate: a sweep across a full field can take hours, and a weight cadence tied to it would starve chain updates whenever the queue is busy.

Two properties of the weight loop are worth stating because both are easy to get wrong. It publishes from the most recent **settled** round rather than the round being evaluated, since building a vector from the in flight set zeroes a submission that was scored last round and is not being re run. And it **holds** rather than publishes while a round is in flight, so a half evaluated ledger never reaches the chain.

Emissions pause is honoured from the directive, so the operator can stop the field without shipping code.

### Reserved emissions

The directive carries `reserved_hotkey` and `reserved_share`. Where a share is set, that fraction of every weight vector is withheld from the field and sent to the reserved hotkey, and the remainder is diluted across whatever the round earned. A share of `1` sends the whole vector there and pays nobody else, which is how the subnet runs before miners are onboarded. Both default to empty and zero, so a directive that says nothing about them pays the field in full.

The reserved hotkey must be registered on the subnet. If it is not, the weight loop refuses to publish rather than letting the withheld share fall through to the miners it was meant to be withheld from, and says so in the log every interval until the registration lands.

The address that governs a running network is never compiled in. It lives in the directive the operator signs, so changing it or winding the share down to zero is a new revision rather than a release.

There is one exception, and it applies only before the network starts. A validator that has never held a directive and has never settled a round has no signed instruction to follow, and would otherwise publish nothing at all. In that state it sends the whole vector to a compiled in fallback reserved hotkey. Any directive overrides it, live or cached, so the constant stops being consulted the moment revision 1 exists and can never displace a signed instruction on a running network. A validator that has scored even one round holds its weights instead when the control plane goes quiet, because by then silence is an outage rather than a network that has not launched.

## Opening a round by hand

```sh
enclave-validator open-round round-042 "$ENTROPY" --opened-at 2026-01-01T00:00:00Z
```

`ENTROPY` must be fixed only after submissions close. A block hash from the closing block is the intended source. Supplying entropy that existed earlier destroys the unpredictability guarantee, because a miner could then have built against the instances.

This reads its prices, families, instance count, and scoring parameters from the directive, exactly as the daemon does. `--local-snapshot` prices from `ENCLAVE_PRICE_SNAPSHOT` instead; it exists for debugging, it warns when used, and a round opened that way will not agree with the field.

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
| Validator disagrees with peers | Compare `header.digest()` and the directive revision first. Prices now arrive signed, so a differing snapshot digest means the two validators were on different revisions when they opened, not that one was misconfigured. Check `effective_from_block` and whether one of them was running from the cached directive. |

`excluded` deserves particular attention. It counts instances dropped as infrastructure faults, and a number that climbs is a validator problem being quietly absorbed rather than charged to submissions.

## Staying current

Every validator must run the same mechanism code. Two validators folding the same evidence into different numbers is a consensus divergence, and consensus penalises both of them for it, so falling behind on a scoring change costs money rather than merely being untidy.

The deploy in `deploy/validator/` runs the validator as a container alongside Watchtower, which pulls a new image and recreates the container in place.

```sh
cd deploy/validator
docker compose up -d
```

`WATCHTOWER_POLL_INTERVAL` defaults to 300 seconds. That is the real convergence window; do not assume a longer one from habit.

### Updates never land mid round

An update that swapped the scoring code halfway through a round would produce a ledger whose early records were folded by one mechanism and whose later records were folded by another. That round would be incoherent and unreproducible.

The validator therefore takes a lock while a round is in flight, and Watchtower is configured with a pre-update lifecycle hook that reads it:

```text
WATCHTOWER_LIFECYCLE_HOOKS=true
com.centurylinklabs.watchtower.lifecycle.pre-update=/usr/local/bin/enclave-pre-update
```

The hook runs `enclave-validator status --quiet`, which exits `75` while a round is in flight. A non-zero pre-update exit tells Watchtower to skip this container, so the update simply waits for the next poll and lands once the round is finished. The hook is deliberately the only thing standing between an update and a corrupted round, so it fails closed: if it cannot determine the state, it defers.

Check the state yourself at any time:

```sh
enclave-validator status
```

```text
mechanism_version  1
state_root         ./state
round              round-042 (held, pid 1381)
opened_at          2026-01-01T00:00:00Z
```

A lock whose holding process has died is ignored, and any lock older than 24 hours is treated as abandoned, so a validator killed mid round does not block its own updates forever. If you need to clear one by hand, delete `state/round.lock` while no round is running.

### Pinning a version

`ENCLAVE_IMAGE` selects the image. Leaving it unset tracks `:latest`, which is what keeps the field converged. Pinning it to a digest freezes this validator, which is occasionally the right call while debugging but is not a state to sit in, because a validator running an older mechanism scores differently from the field and is clipped for it.

Two things worth knowing about this arrangement. The validator container mounts the Docker socket because it launches submission sandboxes, and Watchtower mounts it because that is how it recreates containers. Anyone who can push the image tag therefore has root equivalent control of every validator host that tracks it, so treat the registry credential as a production deploy key. Watchtower also swaps the image and nothing else: it does not re-read `.env` or change the compose topology, so a configuration change still needs `docker compose up -d` by hand.

## Cadence

Scoring and weight setting are separate concerns and should not share a loop. A sweep across a full field can take hours; a weight cadence tied to it starves chain updates whenever the queue is busy. Weight publication should run on its own schedule, bounded below by the subnet's weight rate limit, and publish the most recent complete fold rather than waiting for the sweep in flight.

Weights are set from the rolling ledger, not from the round currently being evaluated. Building a vector from the in flight set zeroes a submission that was scored last round and is not being re run this round, which is a bug that is easy to introduce and hard to notice, because it looks like ordinary rank movement.

## Cost control

The worst case spend for a round is `submissions * instances * spend_cap`, and it falls on the submissions, not on you. A validator meters what crosses the relay and prices it from the directive; it never pays for it. The number still matters, because it is what a miner is committing to when they submit, and because the spend cap is the denominator every yield is divided by.

Dry run mode substitutes a deterministic provider that makes no network call and costs nothing. It exercises the entire path, including metering and scoring, and is the correct way to validate a configuration change before spending real money on it.
