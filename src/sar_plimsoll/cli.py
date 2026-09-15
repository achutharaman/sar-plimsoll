"""`plimsoll` command line."""

import json
from pathlib import Path
from typing import Annotated

import typer

from sar_plimsoll.config import get_settings
from sar_plimsoll.llm.ledger import CostLedger
from sar_plimsoll.logging_setup import configure_logging
from sar_plimsoll.review.languages import validate_submission
from sar_plimsoll.rules.ingest import current_corpus_version
from sar_plimsoll.wiring import build_container

app = typer.Typer(no_args_is_help=True, add_completion=False)
rules_app = typer.Typer(no_args_is_help=True, help="Manage the historical rules corpus.")
app.add_typer(rules_app, name="rules")


@rules_app.command("ingest")
def rules_ingest(
    csv_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    show_rejected: Annotated[int, typer.Option(help="How many rejected rows to print")] = 20,
    job: Annotated[
        bool, typer.Option(help="Run as an ingest job (Cloud Run Job in GCP) and poll until done")
    ] = False,
) -> None:
    """Parse, embed, load and index a rules CSV (id, type, description). Safe to re-run."""
    configure_logging("WARNING")
    container = build_container(get_settings())
    if job:
        _ingest_as_job(container, csv_path)
        return
    report = container.ingestor.ingest(csv_path.read_bytes())
    data = report.to_dict()
    data["rejected"] = [vars(r) for r in report.rejected[:show_rejected]]
    typer.echo(json.dumps(data, indent=2, default=str))
    if not report.complete:
        raise typer.Exit(code=1)


def _ingest_as_job(container, csv_path: Path) -> None:
    import time

    submitted = container.ingest_jobs.submit(
        uid="cli", filename=csv_path.name, data=csv_path.read_bytes()
    )
    typer.echo(f"ingest job {submitted['id']} started")
    while True:
        current = container.ingest_jobs.get(submitted["id"]) or {}
        state = current.get("status")
        progress = current.get("progress") or {}
        typer.echo(
            f"  status={state} committed={progress.get('committed_rows', 0)} "
            f"remaining={progress.get('remaining', '?')}"
        )
        if state in ("done", "failed"):
            typer.echo(json.dumps(current.get("report"), indent=2, default=str))
            if state == "failed":
                typer.echo(f"error: {current.get('error')}")
                raise typer.Exit(code=1)
            return
        time.sleep(10)


@rules_app.command("status")
def rules_status() -> None:
    """Show the current rules corpus version and size."""
    container = build_container(get_settings())
    typer.echo(
        json.dumps(
            container.store.get_meta("rules_corpus") or {"version": "empty"}, indent=2, default=str
        )
    )


@app.command("review")
def review(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    language: Annotated[str | None, typer.Option(help="Override language detection")] = None,
    rules: Annotated[
        Path | None, typer.Option(help="Ingest this CSV first (useful offline)")
    ] = None,
    baseline: Annotated[bool, typer.Option(help="Send everything to the escalation model")] = False,
) -> None:
    """Review one file directly (no API, no queue) and print findings, score and cost."""
    configure_logging("WARNING")
    settings = get_settings()
    container = build_container(settings)
    if rules:
        container.ingestor.ingest(rules.read_bytes())
    source = validate_submission(
        filename=path.name,
        content=path.read_bytes(),
        declared_language=language,
        max_bytes=settings.max_file_bytes,
        max_lines=settings.max_file_lines,
    )
    ledger = CostLedger()
    outcome = container.orchestrator.run(
        source,
        ledger=ledger,
        corpus_version=current_corpus_version(container.store),
        mode="baseline" if baseline else "tiered",
    )
    summary = ledger.summary()
    typer.echo(
        json.dumps(
            {
                "file": source.filename,
                "language": source.language,
                **outcome.to_record(),
                "cost": summary.model_dump(exclude={"entries"}),
            },
            indent=2,
            default=str,
        )
    )


@app.command("benchmark")
def benchmark(
    paths: Annotated[list[Path], typer.Argument(exists=True, dir_okay=False, readable=True)],
    workers: Annotated[int, typer.Option(help="Files reviewed in parallel")] = 3,
) -> None:
    """Measure the two-tier pipeline against an all-Pro baseline on the same files (no cache).

    Both runs are written to the analytics warehouse as benchmark_tiered / benchmark_baseline, so
    /v1/stats and the cost panel show measured savings rather than an estimate.
    """
    import time
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime

    from sar_plimsoll.analytics.records import review_row
    from sar_plimsoll.analytics.stats import aggregate_rows, build_stats
    from sar_plimsoll.review.prompts import PROMPT_VERSION

    configure_logging("WARNING")
    settings = get_settings()
    container = build_container(settings)
    corpus = current_corpus_version(container.store)

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    ensure = getattr(container.analytics, "ensure_schema", None)
    if ensure:
        ensure(force=True)

    def run(path: Path) -> list[dict]:
        source = validate_submission(
            filename=path.name,
            content=path.read_bytes(),
            declared_language=None,
            max_bytes=settings.max_file_bytes,
            max_lines=settings.max_file_lines,
        )
        pending = []
        for mode in ("tiered", "baseline"):
            review = {
                "id": uuid.uuid4().hex,
                "uid": "benchmark",
                "filename": source.filename,
                "language": source.language,
                "line_count": source.line_count,
                "created_at": datetime.now(UTC),
                "rubric_version": container.rubric.version,
                "prompt_version": PROMPT_VERSION,
                "rules_corpus_version": corpus,
                "models": {
                    "triage": settings.triage_model,
                    "escalation": settings.escalation_model,
                },
            }
            ledger = CostLedger()
            started = time.monotonic()
            try:
                outcome = container.orchestrator.run(
                    source, ledger=ledger, corpus_version=corpus, mode=mode
                )
            except Exception as exc:
                # Paired comparison: a file counts only if both modes produced a review.
                typer.echo(
                    f"  {path.name} [{mode}] failed, file excluded: {type(exc).__name__}: {str(exc)[:120]}"
                )
                return []
            pending.append(
                dict(
                    review=review,
                    status="done",
                    result=outcome.to_record(),
                    ledger=ledger,
                    wall_ms=int((time.monotonic() - started) * 1000),
                    escalations=outcome.escalations,
                    completed_at=datetime.now(UTC),
                    mode=f"benchmark_{mode}",
                    run_id=run_id,
                )
            )
        rows = []
        for kwargs in pending:
            container.runner.emit(**kwargs)
            rows.append(
                review_row(
                    **{k: v for k, v in kwargs.items() if k != "ledger"},
                    cache_hit=False,
                    entries=kwargs["ledger"].entries,
                    prices=container.prices,
                )
            )
        return rows

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        rows = [row for result in pool.map(run, paths) for row in result]

    typer.echo(f"{'file':32} {'mode':20} {'score':>5} {'esc':>3} {'cost_usd':>9} {'wall_s':>6}")
    for row in sorted(rows, key=lambda r: (r["filename"], r["mode"])):
        typer.echo(
            f"{row['filename'][:32]:32} {row['mode']:20} {row['score']:>5} "
            f"{'Y' if row['escalated'] else 'n':>3} {row['cost_usd']:>9.5f} {row['wall_ms'] / 1000:>6.1f}"
        )
    stats = build_stats(aggregate_rows(rows), scope="benchmark", days=0)
    typer.echo(f"run {run_id}: {len(rows) // 2} of {len(paths)} files paired")
    typer.echo(json.dumps(stats["benchmark"], indent=2))


if __name__ == "__main__":
    app()
