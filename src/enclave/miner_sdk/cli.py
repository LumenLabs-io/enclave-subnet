from __future__ import annotations

import json

import typer

from enclave.chain.commitment import commit
from enclave.relay.protocol import STRIPPED_FIELDS, Method
from enclave.sandbox.spec import DIGEST_PATTERN, SandboxSpec

app = typer.Typer(add_completion=False, help="Prepare and verify an Enclave submission.")


@app.command("commit-image")
def commit_image(
    hotkey: str = typer.Argument(..., help="Your miner hotkey"),
    image: str = typer.Argument(..., help="Digest-pinned image, name@sha256:<64 hex>"),
) -> None:
    if not DIGEST_PATTERN.match(image):
        typer.echo("image must be digest-pinned as name@sha256:<64 hex>", err=True)
        raise typer.Exit(code=1)

    commitment, reveal = commit(hotkey, image)
    typer.echo("commit this before the deadline:")
    typer.echo(commitment.payload())
    typer.echo("")
    typer.echo("keep this secret until the reveal window, then publish it:")
    typer.echo(json.dumps(reveal.as_json(), sort_keys=True))


@app.command()
def contract() -> None:
    typer.echo("transport   newline-delimited JSON-RPC over a unix domain socket")
    typer.echo("socket      $ENCLAVE_SOCKET (bind-mounted by the validator)")
    typer.echo("scratch     $ENCLAVE_SCRATCH (tmpfs, discarded at teardown)")
    typer.echo("")
    typer.echo("methods")
    for method in sorted(Method.ALL):
        typer.echo(f"  {method}")
    typer.echo("")
    typer.echo(f"stripped    {', '.join(sorted(STRIPPED_FIELDS))}")
    typer.echo("")
    typer.echo("the container has no network egress; the relay holds the credential")
    typer.echo("observations are billed as tokens, so reading is not free")
    typer.echo("an answer that never crossed the relay is a protocol violation")


@app.command()
def isolation(
    image: str = typer.Argument(..., help="Digest-pinned image to describe"),
) -> None:
    from pathlib import Path

    spec = SandboxSpec(image=image, socket_dir=Path("/enclave"))
    for key, value in spec.isolation_report().items():
        typer.echo(f"{key:<22}{value}")


if __name__ == "__main__":
    app()
