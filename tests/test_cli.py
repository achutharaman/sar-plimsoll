import json

from typer.testing import CliRunner

from sar_plimsoll import cli
from tests.helpers import CLEAN_PY, VULNERABLE_PY, local_container


def test_benchmark_writes_both_modes_and_reports_savings(tmp_path, monkeypatch):
    container = local_container()
    monkeypatch.setattr(cli, "build_container", lambda settings: container)
    monkeypatch.setattr(cli, "get_settings", lambda: container.settings)
    (tmp_path / "vuln.py").write_text(VULNERABLE_PY)
    (tmp_path / "clean.py").write_text(CLEAN_PY)

    result = CliRunner().invoke(
        cli.app, ["benchmark", str(tmp_path / "vuln.py"), str(tmp_path / "clean.py")]
    )
    assert result.exit_code == 0, result.output
    modes = sorted(r["mode"] for r in container.analytics.review_rows)
    assert modes == [
        "benchmark_baseline",
        "benchmark_baseline",
        "benchmark_tiered",
        "benchmark_tiered",
    ]
    summary = json.loads(result.output[result.output.index("{") :])
    assert summary["tiered_reviews"] == 2 and summary["baseline_reviews"] == 2
    assert summary["savings_pct"] is not None


def test_benchmark_excludes_files_where_either_mode_failed(tmp_path, monkeypatch):
    from sar_plimsoll.worker.orchestrator import ReviewFailed

    container = local_container()
    real_run = container.orchestrator.run

    def fail_baseline_for_clean(source, **kw):
        if kw.get("mode") == "baseline" and source.filename == "clean.py":
            raise ReviewFailed("truncated")
        return real_run(source, **kw)

    container.orchestrator.run = fail_baseline_for_clean
    monkeypatch.setattr(cli, "build_container", lambda settings: container)
    monkeypatch.setattr(cli, "get_settings", lambda: container.settings)
    (tmp_path / "vuln.py").write_text(VULNERABLE_PY)
    (tmp_path / "clean.py").write_text(CLEAN_PY)

    result = CliRunner().invoke(
        cli.app, ["benchmark", str(tmp_path / "vuln.py"), str(tmp_path / "clean.py")]
    )
    assert result.exit_code == 0, result.output
    assert "1 of 2 files paired" in result.output
    assert sorted(r["filename"] for r in container.analytics.review_rows) == ["vuln.py", "vuln.py"]


def test_stats_use_only_the_latest_benchmark_run():
    from sar_plimsoll.analytics.stats import build_stats

    container = local_container()
    base = {
        "uid": "benchmark",
        "status": "done",
        "cache_hit": False,
        "escalated": False,
        "score": 5.0,
        "wall_ms": 10,
        "created_at": "2026-09-14T00:00:00+00:00",
    }
    old = [
        base
        | {
            "mode": "benchmark_tiered",
            "run_id": "r1",
            "cost_usd": 0.09,
            "cost_usd_standard": 0.09,
            "completed_at": "2026-09-14T01:00:00+00:00",
        },
        base
        | {
            "mode": "benchmark_baseline",
            "run_id": "r1",
            "cost_usd": 0.10,
            "cost_usd_standard": 0.10,
            "completed_at": "2026-09-14T01:00:00+00:00",
        },
    ]
    new = [
        base
        | {
            "mode": "benchmark_tiered",
            "run_id": "r2",
            "cost_usd": 0.02,
            "cost_usd_standard": 0.02,
            "completed_at": "2026-09-14T02:00:00+00:00",
        },
        base
        | {
            "mode": "benchmark_baseline",
            "run_id": "r2",
            "cost_usd": 0.08,
            "cost_usd_standard": 0.08,
            "completed_at": "2026-09-14T02:00:00+00:00",
        },
    ]
    container.analytics.review_rows.extend(old + new)
    bench = build_stats(container.analytics.mode_aggregates(None, 30), scope="system", days=30)[
        "benchmark"
    ]
    assert bench["tiered_mean_cost_usd"] == 0.02 and bench["savings_pct"] == 0.75
