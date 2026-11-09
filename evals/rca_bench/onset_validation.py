"""Does onset detection recover the symptom start well enough for the temporal rule to be safe?

The benchmark's scenarios give each signal as a single point at its onset, so the temporal rule never had to find an
onset. This module rebuilds each signal as a realistic series (normal samples, noise, then a step or a ramp up to the
scenario's value) over a lookback window, runs ``agents.diagnosis.onset.detect_onset`` on it, and compares the rule's
output with the one it gives when the true onset is known. No model calls.

What matters, in this order:
1. The detected onset must never be so early that it rules out the true culprit ("culprit ruled out" must be 0).
2. False alarms on series with no incident should be rare, because one false early alarm in any of the series moves
   the earliest symptom earlier.
3. Of the changes the rule would rule out with the true onset, how many it still rules out ("recall").

This is evidence about the detector on synthetic series with Gaussian or spiky noise. It says nothing about real
telemetry, which has seasonality, bursts and gaps.

    python -m evals.rca_bench.onset_validation
"""

from __future__ import annotations

import argparse
import random
import statistics
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents.diagnosis.attribution import symptom_onset
from agents.diagnosis.onset import OnsetConfig, condense_evidence, detect_onset
from evals.rca_bench.runner import load_suite

if TYPE_CHECKING:
    from evals.rca_bench.schema import Scenario

SUITES = ("synthetic_v0.jsonl", "synthetic_v0_seed1.jsonl", "synthetic_v0_seed2.jsonl")
SUITE_DIR = Path(__file__).parent / "suites"
BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)  # the alert time; only offsets matter


@dataclass(frozen=True)
class Condition:
    window_s: int
    noise: float  # noise standard deviation as a fraction of the level it sits on
    shape: str  # "step", or "ramp<k>" for a ramp over k samples
    kind: str = "gaussian"  # "gaussian", or "spiky" (gaussian plus rare single-sample outliers)

    @property
    def step_s(self) -> int:
        return max(15, self.window_s // 30)  # what the Prometheus adapter uses for its range queries


def _noise(rng: random.Random, sigma: float, kind: str) -> float:
    value = rng.gauss(0.0, sigma)
    if kind == "spiky" and rng.random() < 0.02:
        value += 10.0 * sigma * rng.choice((-1, 1))
    return value


def build_series(
    onset_s: int,
    baseline: float,
    peak: float,
    condition: Condition,
    rng: random.Random,
) -> list[tuple[datetime, float]]:
    """Samples from ``-window`` to the alert (offset 0), with the departure starting at ``onset_s`` seconds."""
    ramp = int(condition.shape[4:]) if condition.shape.startswith("ramp") else 0
    samples: list[tuple[datetime, float]] = []
    for offset in range(-condition.window_s, 1, condition.step_s):
        if offset < onset_s:
            level = baseline
            sigma = condition.noise * abs(baseline)
        else:
            index = (offset - onset_s) // condition.step_s
            progress = 1.0 if ramp == 0 else min(1.0, (index + 1) / (ramp + 1))
            level = baseline + (peak - baseline) * progress
            sigma = condition.noise * 0.5 * abs(peak)
        samples.append((BASE_TIME + timedelta(seconds=offset), level + _noise(rng, sigma, condition.kind)))
    return samples


@dataclass
class Tally:
    scenarios: int = 0
    onset_in_window: int = 0  # the true onset has at least a baseline's worth of samples before it
    detected: int = 0  # an onset estimate was produced for the scenario
    early: int = 0  # the estimate is earlier than the true onset
    late_errors_s: list[float] = field(default_factory=list)
    culprit_ruled_out: int = 0
    ideal_ruled: int = 0
    still_ruled: int = 0
    extra_ruled: int = 0  # changes ruled out that the true onset would not rule out


def series_evidence(entity: str, metric: str, samples: list[tuple[datetime, float]], ref: str) -> list[dict[str, Any]]:
    """Samples as the evidence records the collector would produce for a range query."""
    return [
        {
            "source": "prometheus",
            "entity": entity,
            "query_or_ref": ref,
            "metric_name": metric,
            "signal_type": "metric",
            "observed_at": t.isoformat(),
            "event_time": t.isoformat(),
            "value": v,
            "description": f"Observed {metric}={v}",
            "tags": {},
        }
        for t, v in samples
    ]


def evaluate(
    scenarios: list[Scenario], condition: Condition, seeds: int, config: OnsetConfig, seed_offset: int = 0
) -> Tally:
    """Run the production path (condense, then the earliest symptom time) on rebuilt series for every scenario."""
    tally = Tally()
    minimum_lead_s = config.min_baseline * condition.step_s
    for scenario in scenarios:
        if not scenario.signals:
            continue
        true_onset = min(s.offset_s for s in scenario.signals)
        ideal = {c.change_id for c in scenario.changes if c.offset_s > true_onset}
        for seed in range(seed_offset, seed_offset + seeds):
            rng = random.Random(f"{scenario.scenario_id}:{seed}:{condition}")  # noqa: S311 - reproducible noise, not security
            evidence: list[dict[str, Any]] = []
            for signal in scenario.signals:
                baseline = signal.baseline if signal.baseline is not None else signal.value * 0.1
                series = build_series(signal.offset_s, baseline, signal.value, condition, rng)
                evidence += series_evidence(signal.service, signal.metric, series, signal.evidence_id)
            condensed = condense_evidence(evidence, config)
            onset = symptom_onset(condensed)
            estimated = any((r.get("tags") or {}).get("onset_estimated") for r in condensed)
            estimate = (onset - BASE_TIME).total_seconds() if onset is not None else 0.0
            tally.scenarios += 1
            tally.onset_in_window += true_onset - (-condition.window_s) >= minimum_lead_s
            if estimated:
                tally.detected += 1
                if estimate < true_onset:
                    tally.early += 1
                else:
                    tally.late_errors_s.append(estimate - true_onset)
            # Without an estimate the earliest symptom time is the latest sample, so nothing is ruled out.
            ruled = {c.change_id for c in scenario.changes if c.offset_s > estimate}
            culprit = scenario.ground_truth.culprit_change_id
            tally.culprit_ruled_out += culprit is not None and culprit in ruled
            tally.ideal_ruled += len(ideal)
            tally.still_ruled += len(ideal & ruled)
            tally.extra_ruled += len(ruled - ideal)
    return tally


def false_alarm_rate(condition: Condition, series: int, config: OnsetConfig, seed_offset: int = 0) -> float:
    """Share of incident-free series (noise around a constant) on which a departure is reported."""
    rng = random.Random(f"null:{condition}:{seed_offset}")  # noqa: S311 - reproducible noise, not security
    hits = 0
    for _ in range(series):
        level = rng.uniform(0.1, 500.0)
        samples = [
            (BASE_TIME + timedelta(seconds=o), level + _noise(rng, condition.noise * level, condition.kind))
            for o in range(-condition.window_s, 1, condition.step_s)
        ]
        hits += detect_onset(samples, config).status == "detected"
    return hits / series


def render(
    scenarios: list[Scenario],
    conditions: list[Condition],
    seeds: int,
    null_series: int,
    cfg: OnsetConfig,
    seed_offset: int = 0,
) -> str:
    lines = [
        "| window | noise | shape | kind | onset has baseline | detected | early | median late (s) "
        "| culprit ruled out | ideal rule kept | extra ruled out | false alarms |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for condition in conditions:
        t = evaluate(scenarios, condition, seeds, cfg, seed_offset)
        median_late = statistics.median(t.late_errors_s) if t.late_errors_s else float("nan")
        recall = t.still_ruled / t.ideal_ruled if t.ideal_ruled else float("nan")
        lines.append(
            f"| {condition.window_s} | {condition.noise:g} | {condition.shape} | {condition.kind} "
            f"| {t.onset_in_window / t.scenarios:.2f} | {t.detected / t.scenarios:.2f} | {t.early} "
            f"| {median_late:.0f} | {t.culprit_ruled_out} | {recall:.2f} | {t.extra_ruled} "
            f"| {false_alarm_rate(condition, null_series, cfg, seed_offset):.3f} |"
        )
    return "\n".join(lines)


def load_all() -> list[Scenario]:
    return [s for name in SUITES for s in load_suite(SUITE_DIR / name)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--windows", type=int, nargs="+", default=[900, 3600, 5400])
    parser.add_argument("--noise", type=float, nargs="+", default=[0.05, 0.15, 0.3])
    parser.add_argument("--shapes", nargs="+", default=["step", "ramp8"])
    parser.add_argument("--kinds", nargs="+", default=["gaussian", "spiky"])
    parser.add_argument("--seeds", type=int, default=10, help="noise draws per scenario and condition")
    parser.add_argument("--null-series", type=int, default=500, help="incident-free series per condition")
    parser.add_argument("--lag", type=int, default=OnsetConfig().safety_lag_samples, help="safety lag in samples")
    parser.add_argument("--seed-offset", type=int, default=0, help="use noise draws numbered from this value")
    args = parser.parse_args()
    conditions = [
        Condition(w, n, s, k) for w in args.windows for n in args.noise for s in args.shapes for k in args.kinds
    ]
    scenarios = load_all()
    print(f"{sum(1 for s in scenarios if s.signals)} scenarios with signals; {args.seeds} noise draws each")
    print(f"safety lag: {args.lag} sample(s); noise draws from {args.seed_offset}\n")
    config = replace(OnsetConfig(), safety_lag_samples=args.lag)
    print(render(scenarios, conditions, args.seeds, args.null_series, config, args.seed_offset))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
