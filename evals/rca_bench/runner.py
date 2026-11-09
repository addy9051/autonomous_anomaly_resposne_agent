"""Suite I/O, the run loop and report rendering."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from evals.rca_bench.metrics import SCORING_VERSION, Metrics, Pair, compute_metrics, is_correct
from evals.rca_bench.schema import SCHEMA_VERSION, Prediction, Scenario

if TYPE_CHECKING:
    from collections.abc import Callable

    from evals.rca_bench.solvers import Solver

    ProgressCallback = Callable[[int, int, Scenario, Prediction, float], None]

DEFAULT_SUITE = Path(__file__).parent / "suites" / "synthetic_v0.jsonl"


def save_suite(scenarios: list[Scenario], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(s.model_dump(mode="json"), sort_keys=True) for s in scenarios]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def load_suite(path: Path = DEFAULT_SUITE) -> list[Scenario]:
    scenarios = [
        Scenario.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    ids = [s.scenario_id for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate scenario ids")
    return scenarios


def suite_sha256(path: Path = DEFAULT_SUITE) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def append_checkpoint(path: Path, suite_sha: str, prediction: Prediction) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"suite_sha256": suite_sha, "prediction": prediction.model_dump(mode="json")})
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")


def load_checkpoint(path: Path, suite_sha: str) -> dict[str, Prediction]:
    """Completed predictions from an earlier run of the same suite. Errored runs are retried, not reused."""
    if not path.exists():
        return {}
    done: dict[str, Prediction] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("suite_sha256") != suite_sha:
            continue
        prediction = Prediction.model_validate(record["prediction"])
        if not prediction.error:
            done[prediction.scenario_id] = prediction
    return done


async def run_solver(
    solver: Solver,
    scenarios: list[Scenario],
    *,
    completed: dict[str, Prediction] | None = None,
    on_prediction: ProgressCallback | None = None,
    timeout_s: float | None = None,
    concurrency: int = 1,
) -> tuple[list[Prediction], Metrics]:
    """Solve scenarios, up to ``concurrency`` at a time, returning predictions in suite order.

    Scenarios already in ``completed`` are reused. A scenario that exceeds ``timeout_s`` (counted from when it
    starts, not while it waits for a slot) is recorded as an error, which always counts as incorrect, so a hung
    endpoint cannot stall the run or inflate a score. The solver must be safe to call concurrently when
    ``concurrency`` is above 1. Per-scenario latency under concurrency includes contention for the shared model
    server, so it is not comparable with a sequential run. ``on_prediction`` runs on the event loop with no
    awaits between completions, so writing a checkpoint line from it is safe.
    """
    reuse = completed or {}
    total = len(scenarios)
    results: list[Prediction | None] = [reuse.get(s.scenario_id) for s in scenarios]
    finished = sum(1 for r in results if r is not None)
    gate = asyncio.Semaphore(max(1, concurrency))

    async def work(index: int, scenario: Scenario) -> None:
        nonlocal finished
        async with gate:
            started = time.perf_counter()
            try:
                prediction = await asyncio.wait_for(solver.solve(scenario), timeout=timeout_s)
            except TimeoutError:
                prediction = Prediction(scenario_id=scenario.scenario_id, error=f"timeout after {timeout_s}s")
        results[index] = prediction
        finished += 1
        if on_prediction:
            on_prediction(finished, total, scenario, prediction, time.perf_counter() - started)

    try:
        await asyncio.gather(*(work(i, s) for i, s in enumerate(scenarios) if results[i] is None))
    finally:
        close = getattr(solver, "close", None)
        if callable(close):
            close()
    predictions = [r for r in results if r is not None]
    metrics = compute_metrics([Pair(s, p) for s, p in zip(scenarios, predictions, strict=True)])
    return predictions, metrics


def build_report(
    solver_name: str,
    scenarios: list[Scenario],
    predictions: list[Prediction],
    metrics: Metrics,
    suite_path: Path,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "harness_schema_version": SCHEMA_VERSION,
        "scoring_version": SCORING_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "solver": solver_name,
        "options": options or {},
        "suite": {
            "path": str(suite_path),
            "sha256": suite_sha256(suite_path),
            "n": len(scenarios),
            "sources": sorted({s.source for s in scenarios}),
        },
        "metrics": asdict(metrics),
        "predictions": [p.model_dump(mode="json") for p in predictions],
    }


METRIC_ROWS: list[tuple[str, str]] = [
    ("top1_change_accuracy", "Top-1 change accuracy"),
    ("top3_change_accuracy", "Top-3 change accuracy"),
    ("category_accuracy", "Category accuracy"),
    ("abstention_recall", "Abstains when unanswerable"),
    ("false_abstention_rate", "Abstains when answerable"),
    ("confident_error_rate", "Confident (>=0.7) errors"),
    ("low_confidence_when_unanswerable", "Low confidence (<=0.4) when unanswerable"),
    ("low_confidence_when_answerable", "Low confidence (<=0.4) when answerable"),
    ("ece", "Calibration error (ECE, lower is better)"),
    ("brier", "Brier score (lower is better)"),
    ("coverage", "Coverage (answered)"),
    ("citation_validity", "Valid citations"),
    ("grounded_correct_rate", "Correct answers citing real support"),
    ("error_rate", "Run errors"),
]


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def render_markdown(results: dict[str, Metrics], suite_path: Path, n: int) -> str:
    names = list(results)
    header = "| Metric | " + " | ".join(names) + " |"
    divider = "|---|" + "---|" * len(names)
    rows = [header, divider]
    for attr, label in METRIC_ROWS:
        rows.append(f"| {label} | " + " | ".join(_fmt(getattr(results[name], attr)) for name in names) + " |")
    first = next(iter(results.values()))
    return (
        f"Suite: `{suite_path.name}` ({n} scenarios, {first.n_answerable} answerable, "
        f"{first.n_unanswerable} unanswerable)\n\n" + "\n".join(rows)
    )


def _spread(values: list[float]) -> str:
    if not values:
        return "n/a"
    if len(values) == 1:
        return f"{values[0]:.3f}"
    return f"{sum(values) / len(values):.3f} ({min(values):.3f}-{max(values):.3f})"


def summarize_reports(paths: list[Path]) -> str:
    """Markdown summary of one or more report files. With several runs it shows mean and (min-max)."""
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    shas = {r["suite"]["sha256"] for r in reports}
    solvers = sorted({r["solver"] for r in reports})
    first = reports[0]
    lines = [
        f"Solver: {', '.join(solvers)} | runs: {len(reports)} | scenarios per run: {first['suite']['n']} "
        f"| suite sha256: {first['suite']['sha256'][:12]}",
    ]
    if first.get("options"):
        lines.append("Options (first report): " + ", ".join(f"{k}={v}" for k, v in sorted(first["options"].items())))
    if len(shas) > 1:
        lines.append("WARNING: reports were produced from different suites and are not comparable.")
    if len({r.get("scoring_version", 1) for r in reports}) > 1:
        lines.append("WARNING: reports use different scoring versions and are not comparable.")
    lines += ["", "| Metric | " + ("Value" if len(reports) == 1 else "Mean (min-max)") + " |", "|---|---|"]
    for attr, label in METRIC_ROWS:
        values = [r["metrics"][attr] for r in reports if r["metrics"].get(attr) is not None]
        lines.append(f"| {label} | {_spread(values)} |")
    latencies = [r["metrics"]["mean_latency_s"] for r in reports if r["metrics"].get("mean_latency_s")]
    lines.append(f"| Mean latency per scenario (s) | {_spread(latencies)} |")

    categories: dict[str, int] = {}
    for report in reports:
        for name, count in (report["metrics"].get("predicted_categories") or {}).items():
            categories[name] = categories.get(name, 0) + count
    if categories:
        lines += [
            "",
            "Predicted categories (all runs): " + ", ".join(f"{k} {v}" for k, v in sorted(categories.items())),
        ]

    classes = sorted({c for r in reports for c in r["metrics"]["by_fault_class"]})
    lines += ["", "| Fault class | n | Accuracy (right answer, or abstained when unanswerable) |", "|---|---|---|"]
    for name in classes:
        entries = [r["metrics"]["by_fault_class"][name] for r in reports if name in r["metrics"]["by_fault_class"]]
        accuracy = [e["accuracy"] for e in entries if e["accuracy"] is not None]
        lines.append(f"| {name} | {entries[0]['n']} | {_spread(accuracy)} |")

    suite_path = Path(first["suite"]["path"])
    if len(shas) == 1 and suite_path.exists() and suite_sha256(suite_path) == first["suite"]["sha256"]:
        scenarios = {s.scenario_id: s for s in load_suite(suite_path)}
        misses: dict[str, int] = {}
        for report in reports:
            for raw in report["predictions"]:
                prediction = Prediction.model_validate(raw)
                if not is_correct(Pair(scenarios[prediction.scenario_id], prediction)):
                    misses[prediction.scenario_id] = misses.get(prediction.scenario_id, 0) + 1
        if misses:
            lines += ["", f"Scenarios answered incorrectly (count out of {len(reports)} runs):"]
            lines += [f"- {sid}: {count}" for sid, count in sorted(misses.items(), key=lambda kv: (-kv[1], kv[0]))]
    return "\n".join(lines)
