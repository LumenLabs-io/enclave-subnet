from __future__ import annotations

import contextlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn

import httpx
import typer
from pydantic import ValidationError

from enclave.chain.commitment import SubmissionCommitment, SubmissionReveal, verify_reveal
from enclave.constants import CONTROL_PLANE_URL, MECHANISM_VERSION, OWNER_PUBLIC_KEY
from enclave.control.client import ControlPlane
from enclave.control.directive import Directive
from enclave.environments.prf import AuditKey
from enclave.environments.registry import families as known_families
from enclave.errors import ConfigError, EnclaveError, LedgerError, PricingError, ProtocolError
from enclave.ledger.store import Ledger
from enclave.relay.pricing import PriceSnapshot
from enclave.relay.providers import Provider
from enclave.validator.config import ValidatorSettings
from enclave.validator.round import plan_round
from enclave.validator.state import read_lock, round_in_flight

app = typer.Typer(add_completion=False, help="Operate an Enclave validator.")


def _fail(message: str) -> NoReturn:
    """Report an expected failure as a message rather than a traceback."""
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _settings() -> ValidatorSettings:
    try:
        settings = ValidatorSettings()
    except ValidationError as exc:
        _fail(
            "configuration is invalid. Every setting is ENCLAVE_ prefixed and unknown keys "
            f"in .env are rejected rather than ignored:\n\n{exc}"
        )
    try:
        settings.assert_ready()
    except ConfigError as exc:
        _fail(str(exc))
    return settings


def _provider(settings: ValidatorSettings) -> Provider:
    from enclave.relay.providers import DeterministicProvider

    if not settings.provider_base_url:
        raise typer.BadParameter(
            "ENCLAVE_PROVIDER_BASE_URL must be set, or pass --dry-run to use a stub provider"
        )
    return DeterministicProvider(seed=settings.provider_base_url)


def _snapshot(path: Path) -> PriceSnapshot:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail(f"no price snapshot at {path}")
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"price snapshot {path} could not be read: {exc}")
    try:
        return PriceSnapshot.from_json(raw)
    except PricingError as exc:
        _fail(f"price snapshot {path} is invalid: {exc}")


def _wallet(settings: ValidatorSettings) -> Any:
    try:
        import bittensor
    except ImportError:
        _fail(
            "bittensor is not installed. Install the validator extra:\n"
            '  pip install -e ".[validator]"'
        )
    try:
        return bittensor.wallet(name=settings.wallet_name, hotkey=settings.wallet_hotkey)
    except Exception as exc:
        _fail(
            f"could not open wallet {settings.wallet_name}/{settings.wallet_hotkey}: {exc}\n"
            "Check ENCLAVE_WALLET_NAME and ENCLAVE_WALLET_HOTKEY, and that ~/.bittensor is "
            "readable by this user."
        )


def _control(settings: ValidatorSettings, wallet: Any = None) -> ControlPlane:
    """A signed control plane client.

    The base url and owner key come from the compiled constants rather than from
    settings, so no configuration can point a validator at another network.
    """
    hotkey = (wallet if wallet is not None else _wallet(settings)).hotkey
    return ControlPlane(
        base_url=CONTROL_PLANE_URL,
        hotkey=hotkey.ss58_address,
        sign=lambda message: hotkey.sign(message),
        owner_public_key=OWNER_PUBLIC_KEY,
        netuid=settings.netuid,
    )


def _directive(settings: ValidatorSettings) -> Directive:
    control = _control(settings)
    try:
        return control.directive()
    except ConfigError as exc:
        _fail(
            f"{exc}\n\n"
            f"A hotkey must be admitted by the subnet owner before it can validate. "
            f"Ask the operator to admit {control.hotkey}, then run preflight again."
        )
    except ProtocolError as exc:
        _fail(f"the control plane returned a directive this build cannot use: {exc}")
    except httpx.HTTPError as exc:
        _fail(
            f"could not reach the control plane at {CONTROL_PLANE_URL}: "
            f"{type(exc).__name__}: {exc}"
        )
    finally:
        control.close()


@app.command()
def preflight() -> None:
    """Prove this validator can score a round: admitted, directive verified, prices usable."""
    settings = _settings()
    directive = _directive(settings)
    snapshot = directive.price_snapshot

    typer.echo(f"netuid            {settings.netuid}")
    typer.echo(f"control plane     {CONTROL_PLANE_URL}")
    typer.echo(f"owner key         {OWNER_PUBLIC_KEY}")
    typer.echo(
        f"mechanism         {MECHANISM_VERSION} "
        f"(network minimum {directive.min_mechanism_version})"
    )
    typer.echo(f"directive         revision {directive.revision} ({directive.digest[:16]})")
    typer.echo(f"effective from    block {directive.effective_from_block}")
    typer.echo(f"families          {', '.join(directive.families)}")
    typer.echo(f"known families    {', '.join(known_families())}")
    typer.echo(f"instances/family  {directive.instances_per_family}")
    typer.echo(f"price snapshot    {snapshot.snapshot_id} ({snapshot.digest()[:16]})")
    typer.echo(f"models priced     {', '.join(snapshot.selectable)}")
    typer.echo(f"default model     {directive.default_model}")
    typer.echo(f"spend cap         {directive.spend_cap}")
    typer.echo(f"ledger            {settings.ledger_root}")
    typer.echo(f"dry run           {settings.dry_run}")

    unknown = set(directive.families) - set(known_families())
    if unknown:
        _fail(
            f"the directive enables families this build cannot generate: {sorted(unknown)}. "
            "Update before scoring another round."
        )

    worst_case = directive.spend_cap * directive.instances_per_family * len(directive.families)
    typer.echo(f"worst case spend  {worst_case} per submission")
    typer.echo("preflight ok")


@app.command()
def status(
    state_root: Path = typer.Option(Path("./state"), "--state-root"),
    quiet: bool = typer.Option(False, "--quiet", help="Print nothing; signal by exit code"),
) -> None:
    lock = read_lock(state_root)
    in_flight = round_in_flight(state_root)

    if not quiet:
        typer.echo(f"mechanism_version  {MECHANISM_VERSION}")
        typer.echo(f"state_root         {state_root}")
        if lock is None:
            typer.echo("round              idle")
        else:
            held = "held" if lock.holder_alive else "stale"
            typer.echo(f"round              {lock.round_id} ({held}, pid {lock.pid})")
            typer.echo(f"opened_at          {lock.opened_at}")

    if in_flight:
        raise typer.Exit(code=75)


@app.command()
def run(
    dry_run: bool = typer.Option(False, "--dry-run", help="Use a deterministic provider"),
) -> None:
    import asyncio
    import logging
    import signal

    from enclave.chain.client import BittensorChain
    from enclave.ledger.store import Ledger
    from enclave.relay.providers import DeterministicProvider, HeuristicTokenCounter
    from enclave.sandbox.runner import DockerRunner
    from enclave.sandbox.spec import Limits
    from enclave.validator.daemon import DaemonConfig, Validator

    settings = _settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")

    import bittensor

    wallet = _wallet(settings)
    try:
        subtensor = bittensor.subtensor(network=settings.chain_endpoint)
    except Exception as exc:
        _fail(f"could not connect to the chain at {settings.chain_endpoint}: {exc}")

    control = _control(settings, wallet)

    validator = Validator(
        config=DaemonConfig(
            netuid=settings.netuid,
            socket_root=settings.socket_root,
            state_root=settings.ledger_root.parent,
            limits=Limits(
                cpus=settings.container_cpus,
                memory_bytes=settings.memory_bytes,
                wall_clock_seconds=settings.container_wall_clock_seconds,
            ),
        ),
        control=control,
        chain=BittensorChain(netuid=settings.netuid, wallet=wallet, subtensor=subtensor),
        ledger=Ledger(settings.ledger_root),
        runner=DockerRunner(),
        provider=DeterministicProvider(seed="dry-run") if dry_run else _provider(settings),
        counter=HeuristicTokenCounter(),
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for name in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(AttributeError, NotImplementedError):
            loop.add_signal_handler(getattr(signal, name), validator.stop)
    try:
        loop.run_until_complete(validator.run())
    except KeyboardInterrupt:
        validator.stop()
    finally:
        control.close()
        loop.close()


@app.command("open-round")
def open_round(
    round_id: str = typer.Argument(..., help="Identifier for the round"),
    entropy: str = typer.Argument(..., help="Entropy fixed only after submissions close"),
    opened_at: str = typer.Option(..., "--opened-at", help="ISO-8601 timestamp"),
    local_snapshot: bool = typer.Option(
        False,
        "--local-snapshot",
        help="Price from ENCLAVE_PRICE_SNAPSHOT instead of the directive. Debugging only: "
        "a round opened this way scores differently from the rest of the field.",
    ),
) -> None:
    settings = _settings()

    spend_cap: Decimal | None = None
    failure_penalty: Decimal | None = None
    denominator_floor: Decimal | None = None
    audit_rate: Decimal | None = None

    if local_snapshot:
        typer.secho(
            "WARNING: pricing from the local snapshot; this round will not agree with the field",
            fg=typer.colors.YELLOW,
            err=True,
        )
        snapshot = _snapshot(settings.price_snapshot)
        families: tuple[str, ...] = settings.families
        instances_per_family = settings.instances_per_family
        default_model = settings.default_model
    else:
        directive = _directive(settings)
        snapshot = directive.price_snapshot
        families = directive.families
        instances_per_family = directive.instances_per_family
        default_model = directive.default_model
        spend_cap = directive.spend_cap
        failure_penalty = directive.failure_penalty
        denominator_floor = directive.denominator_floor
        audit_rate = directive.audit_rate

    try:
        plan = plan_round(
            round_id=round_id,
            entropy=entropy,
            opened_at=opened_at,
            snapshot=snapshot,
            families=families,
            instances_per_family=instances_per_family,
            default_model=default_model,
            spend_cap=spend_cap,
            failure_penalty=failure_penalty,
            denominator_floor=denominator_floor,
            audit_rate=audit_rate,
        )
    except EnclaveError as exc:
        _fail(f"the round could not be planned: {exc}")

    ledger = Ledger(settings.ledger_root)
    try:
        ledger.open_round(plan.header)
        (settings.ledger_root / round_id / "audit-key.hex").write_text(
            plan.audit_key.hex(), encoding="utf-8"
        )
    except LedgerError as exc:
        _fail(f"the round could not be opened: {exc}")
    except OSError as exc:
        _fail(f"the ledger at {settings.ledger_root} could not be written: {exc}")

    typer.echo(f"round        {plan.round_id}")
    typer.echo(f"seed         {plan.header.seed}")
    typer.echo(f"header       {plan.header.digest()}")
    typer.echo(f"commitment   {plan.header.audit_commitment}")
    typer.echo(f"prices       {snapshot.snapshot_id} ({snapshot.digest()[:16]})")
    typer.echo(f"instances    {len(plan.specs)}")


@app.command("reveal-audit")
def reveal_audit(
    round_id: str = typer.Argument(...),
    ledger_root: Path = typer.Option(Path("./state/ledger"), "--ledger-root"),
) -> None:
    ledger = Ledger(ledger_root)
    try:
        header = ledger.header(round_id)
    except LedgerError as exc:
        _fail(f"no readable round {round_id} under {ledger_root}: {exc}")

    key_path = ledger_root / round_id / "audit-key.hex"
    try:
        key = AuditKey.from_hex(key_path.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        _fail(f"no audit key at {key_path}; it is written when the round is opened")
    except (OSError, ValueError) as exc:
        _fail(f"the audit key at {key_path} could not be read: {exc}")

    if not key.verify_commitment(header.audit_commitment):
        _fail(
            "audit key does not match the committed digest, so revealing it would prove "
            "nothing about this round"
        )

    audited = [
        record.instance_id
        for record in ledger.records(round_id)
        if key.selects(header.seed, record.instance_id, header.audit_rate)
    ]
    typer.echo(f"secret     {key.hex()}")
    typer.echo(f"commitment {header.audit_commitment}")
    typer.echo(f"audited    {sorted(set(audited))}")


@app.command("verify-reveal")
def verify_submission_reveal(
    commitment_json: str = typer.Argument(..., help="Committed payload"),
    reveal_json: str = typer.Argument(..., help="Revealed payload"),
) -> None:
    try:
        committed = json.loads(commitment_json)
        revealed = json.loads(reveal_json)
        pair = (
            SubmissionCommitment(
                hotkey=str(committed["hotkey"]), commitment=str(committed["commitment"])
            ),
            SubmissionReveal(
                hotkey=str(revealed["hotkey"]),
                image=str(revealed["image"]),
                nonce=str(revealed["nonce"]),
            ),
        )
    except json.JSONDecodeError as exc:
        _fail(f"arguments must be JSON objects: {exc}")
    except KeyError as exc:
        _fail(f"missing field {exc} in the commitment or reveal")

    ok = verify_reveal(*pair)
    typer.echo("reveal matches commitment" if ok else "reveal does NOT match")
    if not ok:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
