from __future__ import annotations

import json
from pathlib import Path

import typer

from enclave.ledger.store import Ledger
from enclave.scoring.allocation import AllocationParams, allocate
from enclave.scoring.yield_score import score_round

app = typer.Typer(
    add_completion=False,
    help="Recompute scores and weights from a published ledger.",
)


@app.command()
def verify(
    ledger_root: Path = typer.Argument(..., help="Directory holding round ledgers"),
    round_id: str = typer.Argument(..., help="Round to recompute"),
) -> None:
    ledger = Ledger(ledger_root)
    intact = ledger.verify(round_id)
    header = ledger.header(round_id)

    typer.echo(f"round        {round_id}")
    typer.echo(f"header       {header.digest()}")
    typer.echo(f"seed         {header.seed}")
    typer.echo(f"records      {len(ledger.records(round_id))}")
    typer.echo(f"hash chain   {'intact' if intact else 'BROKEN'}")

    if not intact:
        raise typer.Exit(code=1)


@app.command()
def score(
    ledger_root: Path = typer.Argument(..., help="Directory holding round ledgers"),
    round_id: str = typer.Argument(..., help="Round to recompute"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable output"),
) -> None:
    ledger = Ledger(ledger_root)
    header = ledger.header(round_id)
    params = header.scoring_params

    scores = {
        submission_id: score_round(
            submission_id, ledger.for_submission(round_id, submission_id), params
        )
        for submission_id in ledger.submissions(round_id)
    }
    allocation = allocate(
        [(sid, s.yield_score) for sid, s in sorted(scores.items()) if s.verdict.value == "pass"],
        AllocationParams(),
    )

    if as_json:
        typer.echo(
            json.dumps(
                {
                    "round_id": round_id,
                    "header_digest": header.digest(),
                    "scores": {
                        sid: {
                            "yield": str(s.yield_score),
                            "solved": s.solved,
                            "scored": s.scored,
                            "charged": str(s.total_charged),
                            "verdict": s.verdict.value,
                            "void_reason": s.void_reason,
                        }
                        for sid, s in sorted(scores.items())
                    },
                    "weights_ppm": allocation.ppm,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    typer.echo(f"{'submission':<28}{'yield':>16}{'solved':>10}{'ppm':>10}")
    for sid, s in sorted(scores.items(), key=lambda kv: -kv[1].yield_score):
        typer.echo(
            f"{sid[:28]:<28}{s.yield_score!s:>16}"
            f"{f'{s.solved}/{s.scored}':>10}{allocation.ppm.get(sid, 0):>10}"
        )


if __name__ == "__main__":
    app()
