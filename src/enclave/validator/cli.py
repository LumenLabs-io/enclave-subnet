from __future__ import annotations

import json
from pathlib import Path

import typer

from enclave.chain.commitment import SubmissionCommitment, SubmissionReveal, verify_reveal
from enclave.constants import MECHANISM_VERSION
from enclave.environments.prf import AuditKey
from enclave.environments.registry import families as known_families
from enclave.ledger.store import Ledger
from enclave.relay.pricing import PriceSnapshot
from enclave.validator.config import ValidatorSettings
from enclave.validator.round import plan_round
from enclave.validator.state import read_lock, round_in_flight

app = typer.Typer(add_completion=False, help="Operate an Enclave validator.")


def _snapshot(path: Path) -> PriceSnapshot:
    return PriceSnapshot.from_json(json.loads(path.read_text(encoding="utf-8")))


@app.command()
def preflight() -> None:
    settings = ValidatorSettings()
    settings.assert_ready()
    snapshot = _snapshot(settings.price_snapshot)

    typer.echo(f"netuid            {settings.netuid}")
    typer.echo(f"families          {', '.join(settings.families)}")
    typer.echo(f"known families    {', '.join(known_families())}")
    typer.echo(f"instances/family  {settings.instances_per_family}")
    typer.echo(f"price snapshot    {snapshot.snapshot_id} ({snapshot.digest()[:16]})")
    typer.echo(f"models priced     {', '.join(snapshot.selectable)}")
    typer.echo(f"default model     {settings.default_model}")
    typer.echo(f"ledger            {settings.ledger_root}")
    typer.echo(f"dry run           {settings.dry_run}")

    unknown = set(settings.families) - set(known_families())
    if unknown:
        typer.echo(f"unknown families: {sorted(unknown)}", err=True)
        raise typer.Exit(code=1)
    if settings.default_model not in snapshot.selectable:
        typer.echo(f"default model {settings.default_model} is not priced", err=True)
        raise typer.Exit(code=1)
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


@app.command("open-round")
def open_round(
    round_id: str = typer.Argument(..., help="Identifier for the round"),
    entropy: str = typer.Argument(..., help="Entropy fixed only after submissions close"),
    opened_at: str = typer.Option(..., "--opened-at", help="ISO-8601 timestamp"),
) -> None:
    settings = ValidatorSettings()
    snapshot = _snapshot(settings.price_snapshot)

    plan = plan_round(
        round_id=round_id,
        entropy=entropy,
        opened_at=opened_at,
        snapshot=snapshot,
        families=settings.families,
        instances_per_family=settings.instances_per_family,
    )

    ledger = Ledger(settings.ledger_root)
    ledger.open_round(plan.header)
    (settings.ledger_root / round_id / "audit-key.hex").write_text(
        plan.audit_key.hex(), encoding="utf-8"
    )

    typer.echo(f"round        {plan.round_id}")
    typer.echo(f"seed         {plan.header.seed}")
    typer.echo(f"header       {plan.header.digest()}")
    typer.echo(f"commitment   {plan.header.audit_commitment}")
    typer.echo(f"instances    {len(plan.specs)}")


@app.command("reveal-audit")
def reveal_audit(
    round_id: str = typer.Argument(...),
    ledger_root: Path = typer.Option(Path("./state/ledger"), "--ledger-root"),
) -> None:
    ledger = Ledger(ledger_root)
    header = ledger.header(round_id)
    key = AuditKey.from_hex((ledger_root / round_id / "audit-key.hex").read_text().strip())

    if not key.verify_commitment(header.audit_commitment):
        typer.echo("audit key does not match the committed digest", err=True)
        raise typer.Exit(code=1)

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
    committed = json.loads(commitment_json)
    revealed = json.loads(reveal_json)
    ok = verify_reveal(
        SubmissionCommitment(
            hotkey=str(committed["hotkey"]), commitment=str(committed["commitment"])
        ),
        SubmissionReveal(
            hotkey=str(revealed["hotkey"]),
            image=str(revealed["image"]),
            nonce=str(revealed["nonce"]),
        ),
    )
    typer.echo("reveal matches commitment" if ok else "reveal does NOT match")
    if not ok:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
