"""Onset detection: dating a symptom so the temporal rule is safe on real telemetry."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agents.diagnosis.attribution import has_symptom_evidence, rule_out_acausal_changes, symptom_onset
from agents.diagnosis.evidence import collect_symptom_evidence
from agents.diagnosis.onset import OnsetConfig, condense_evidence, detect_onset
from shared.schemas import NormalizedTelemetryRecord, TelemetryConnectorSource, TelemetrySignalType

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
STEP = 60


def at(index: int) -> datetime:
    return T0 + timedelta(seconds=index * STEP)


def series(values: list[float]) -> list[tuple[datetime, float]]:
    return [(at(i), v) for i, v in enumerate(values)]


def noisy(level: float, n: int, seed: int = 1, noise: float = 0.05) -> list[float]:
    rng = random.Random(seed)  # noqa: S311 - reproducible test noise
    return [level + rng.gauss(0, noise * level) for _ in range(n)]


def step_series(onset_index: int, total: int = 40, low: float = 1.0, high: float = 5.0, seed: int = 1) -> list[float]:
    return noisy(low, onset_index, seed) + noisy(high, total - onset_index, seed + 1)


def test_a_clean_step_is_dated_at_or_just_after_the_real_onset() -> None:
    result = detect_onset(series(step_series(20)))
    assert result.status == "detected" and result.onset is not None
    assert at(20) <= result.onset <= at(22)  # never before the real departure; at most a sample or two after


def test_a_downward_step_is_detected_too() -> None:
    result = detect_onset(series(noisy(100.0, 20) + noisy(20.0, 20, seed=5)))
    assert result.status == "detected" and result.onset is not None
    assert at(20) <= result.onset <= at(22)


def test_a_flat_zero_baseline_with_a_later_rise_is_dated() -> None:
    result = detect_onset(series([0.0] * 20 + [0.4] * 20))
    assert result.status == "detected" and result.onset is not None
    assert at(20) <= result.onset <= at(22)


def test_noise_alone_is_not_a_departure() -> None:
    assert detect_onset(series(noisy(10.0, 60, noise=0.2))).status == "no_departure"


def test_a_spike_that_recovers_is_not_the_current_incident() -> None:
    values = noisy(1.0, 40)
    values[15:18] = [6.0, 6.0, 6.0]
    assert detect_onset(series(values)).status == "no_departure"


def test_a_lone_spike_right_before_the_onset_does_not_make_the_estimate_early() -> None:
    values = step_series(20)
    values[19] = 4.0  # an outlier directly before the real departure joins the run of departed samples
    result = detect_onset(series(values))
    assert result.onset is not None
    assert result.onset >= at(20)


def test_a_series_already_abnormal_from_its_first_sample_gives_no_estimate() -> None:
    assert detect_onset(series(noisy(5.0, 40))).status == "no_departure"


def test_too_few_samples_for_a_baseline_say_so() -> None:
    result = detect_onset(series(step_series(8, total=14)))
    assert (result.status, result.onset) == ("too_short", None)


def test_non_finite_samples_are_ignored() -> None:
    values = step_series(20)
    values[5] = float("nan")
    assert detect_onset(series(values)).status == "detected"


def _evidence(entity: str, metric: str, values: list[float], start: int = 0) -> list[dict[str, Any]]:
    return [
        {
            "source": "prometheus",
            "entity": entity,
            "query_or_ref": f"{entity}:{metric}",
            "metric_name": metric,
            "signal_type": "metric",
            "observed_at": at(start + i).isoformat(),
            "event_time": at(start + i).isoformat(),
            "value": v,
            "description": f"Observed {metric}={v}",
            "tags": {},
        }
        for i, v in enumerate(values)
    ]


def _change(change_id: str, index: int) -> dict[str, Any]:
    return {
        "source": "github",
        "entity": "checkout",
        "query_or_ref": change_id,
        "metric_name": f"{change_id} change",
        "signal_type": "deploy_event",
        "observed_at": at(index).isoformat(),
        "event_time": at(index).isoformat(),
        "value": 1.0,
        "description": f"{change_id} deployed",
        "tags": {},
    }


def test_point_observations_and_changes_pass_through_unchanged() -> None:
    evidence = [*_evidence("checkout", "error_rate", [0.2, 5.0]), _change("chg-001", 3)]
    assert condense_evidence(evidence) is evidence


def test_a_series_becomes_one_record_at_its_departure_and_changes_are_kept() -> None:
    evidence = sorted(
        [*_evidence("checkout", "error_rate", step_series(20)), _change("chg-001", 19)],
        key=lambda e: e["observed_at"],
    )
    condensed = condense_evidence(evidence)
    assert len(condensed) == 2
    symptom = next(r for r in condensed if r["signal_type"] != "deploy_event")
    assert symptom["tags"]["symptom"] is True and symptom["tags"]["onset_estimated"] is True
    assert at(20).isoformat() <= symptom["event_time"] <= at(22).isoformat()
    assert any(r["signal_type"] == "deploy_event" and r["metric_name"] == "chg-001 change" for r in condensed)


def test_a_series_too_short_to_date_uses_its_latest_sample_not_its_first() -> None:
    condensed = condense_evidence(_evidence("checkout", "error_rate", step_series(5, total=10)))
    (record,) = condensed
    assert record["tags"]["onset_unknown"] is True and record["tags"]["symptom"] is True
    assert record["event_time"] == at(9).isoformat()


def test_a_series_with_no_departure_is_not_a_symptom() -> None:
    (record,) = condense_evidence(_evidence("checkout", "latency_p99", noisy(100.0, 40)))
    assert record["tags"]["symptom"] is False
    assert not has_symptom_evidence([record])
    assert symptom_onset([record]) is None


def test_raw_samples_would_rule_out_a_real_culprit_but_condensed_evidence_does_not() -> None:
    # The change lands at minute 19, one sample before the metric leaves its baseline at minute 20. The earliest
    # raw sample is minute 0, so treating it as the symptom start rules the culprit out.
    raw = sorted(
        [*_evidence("checkout", "error_rate", step_series(20)), _change("chg-001", 19)], key=lambda e: e["observed_at"]
    )
    assert symptom_onset(raw) == at(0)
    assert rule_out_acausal_changes(raw[:40]) != {}  # the unsafe behaviour this module exists to prevent
    condensed = condense_evidence(raw)
    onset = symptom_onset(condensed)
    assert onset is not None and onset >= at(20)
    assert rule_out_acausal_changes(condensed) == {}


def test_a_change_landing_after_the_estimated_onset_is_still_ruled_out() -> None:
    evidence = sorted(
        [*_evidence("checkout", "error_rate", step_series(20)), _change("chg-009", 30)], key=lambda e: e["observed_at"]
    )
    condensed = condense_evidence(evidence)
    ruled = rule_out_acausal_changes(condensed)
    assert len(ruled) == 1 and "after the earliest symptom" in next(iter(ruled.values()))


def test_the_earliest_departure_across_metrics_is_the_symptom_start() -> None:
    evidence = [
        *_evidence("checkout", "latency_p99", step_series(26, high=4.0)),
        *_evidence("checkout", "error_rate", step_series(20)),
    ]
    onset = symptom_onset(condense_evidence(evidence))
    assert onset is not None and at(20) <= onset <= at(22)


def test_the_benchmark_suites_evidence_is_not_altered() -> None:
    from pathlib import Path

    from evals.rca_bench.agent_solver import scenario_evidence
    from evals.rca_bench.runner import load_suite

    suite_dir = Path("evals/rca_bench/suites")
    for name in ("synthetic_v0.jsonl", "synthetic_v0_seed1.jsonl", "synthetic_v0_seed2.jsonl"):
        for scenario in load_suite(suite_dir / name):
            evidence = scenario_evidence(scenario)
            assert condense_evidence(evidence) is evidence, scenario.scenario_id


class _SeriesAdapter:
    source = TelemetryConnectorSource.PROMETHEUS

    def __init__(self, values: list[float], fail: bool = False) -> None:
        self.values = values
        self.fail = fail
        self.window_seconds = 0

    async def fetch_signals(self, entity: str, metric_names: list[str], time_window_seconds: int = 300) -> list[Any]:
        self.window_seconds = time_window_seconds
        if self.fail:
            from data_pipeline.connectors.adapters.base import TelemetrySourceError

            raise TelemetrySourceError("down")
        return [
            NormalizedTelemetryRecord(
                timestamp=at(i),
                entity=entity,
                connector_source=self.source,
                signal_type=TelemetrySignalType.METRIC,
                metric_name=metric,
                value=v,
            )
            for metric in metric_names
            for i, v in enumerate(self.values)
        ]


async def test_collect_symptom_evidence_uses_a_long_window_and_dates_each_metric() -> None:
    adapter = _SeriesAdapter(step_series(20))
    evidence, gaps = await collect_symptom_evidence(adapter, ["checkout"], ["error_rate", "latency_p99"])
    assert adapter.window_seconds == 5400
    assert gaps == []
    assert len(evidence) == 2
    assert all(e["tags"]["onset_estimated"] for e in evidence)
    onset = symptom_onset(evidence)
    assert onset is not None and at(20) <= onset <= at(22)


async def test_collect_symptom_evidence_reports_a_failed_source_as_a_gap_not_a_symptom() -> None:
    evidence, gaps = await collect_symptom_evidence(_SeriesAdapter([], fail=True), ["checkout"], ["error_rate"])
    assert evidence == []
    assert gaps == ["prometheus:checkout:*"]


def test_the_config_requires_a_baseline_before_it_will_date_anything() -> None:
    config = OnsetConfig()
    assert config.needed_samples == config.min_baseline + config.persist
    with pytest.raises(AttributeError):
        config.min_baseline = 1  # type: ignore[misc]
