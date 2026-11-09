"""Estimate when a symptom started, so that the temporal rule can run on real telemetry.

The rule in ``attribution.py`` ("a change that landed after the earliest symptom cannot be the cause") needs the time
a metric first went abnormal. Adapters do not return that: a range query returns every sample in the window, normal
ones included, so the earliest sample is the start of the window. Fed to the rule as it is, that would rule out a
real culprit that landed a few minutes before the alert. This module finds the departure from baseline instead.

Safety property, which the validation in ``evals/rca_bench/onset_validation.py`` measures: an estimate that is too
LATE only makes the rule rule out fewer changes. An estimate that is too EARLY can rule out the true culprit. The
detector therefore leans late (it reports the first sample that is clearly past the baseline, after a sustained
departure) and says nothing when it cannot tell, in which case the rule does not fire.

What it cannot do: it needs baseline samples before the departure. A series that is already abnormal from its first
sample, or a window that starts after the onset, gives no estimate. It works on a single series; seasonality, bursty
traffic and missing samples are not modelled, and it has only been validated on synthetic series.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

DEPLOY_SIGNAL_TYPE = "deploy_event"
MAD_TO_SIGMA = 1.4826

Status = Literal["detected", "no_departure", "too_short"]


@dataclass(frozen=True)
class OnsetConfig:
    """Detector settings. The defaults are deliberately conservative: they trade missed onsets for no early ones."""

    min_samples: int = 3  # this many samples of one metric make a series; fewer are point observations
    min_baseline: int = 12  # samples needed before a departure can be called; fewer made the noise scale unreliable
    threshold_mads: float = 6.0  # robust z-score the departure must reach
    min_relative_change: float = 0.5  # and it must be at least this fraction of the baseline level
    persist: int = 3  # consecutive samples that must stay departed
    sustained_fraction: float = 0.9  # share of samples from the onset to the end that must stay departed
    min_scale_fraction: float = 0.02  # floor for the noise scale, as a fraction of the baseline level
    # Report this many samples after the first departed one. A single outlier right before the real departure
    # joins the run and would pull the estimate one sample early, which is the unsafe direction.
    safety_lag_samples: int = 1

    @property
    def needed_samples(self) -> int:
        return self.min_baseline + self.persist


DEFAULT_CONFIG = OnsetConfig()


@dataclass(frozen=True)
class OnsetResult:
    status: Status
    onset: datetime | None = None
    baseline: float | None = None
    peak: float | None = None
    samples: int = 0
    reason: str = ""


def _departed(value: float, median: float, threshold: float, sign: int) -> bool:
    return (value - median) * sign >= threshold


def detect_onset(samples: Sequence[tuple[datetime, float]], config: OnsetConfig = DEFAULT_CONFIG) -> OnsetResult:
    """The first sample of a sustained departure from the baseline that precedes it, or why there is none."""
    series = sorted((t, v) for t, v in samples if math.isfinite(v))
    n = len(series)
    if n < config.needed_samples:
        return OnsetResult("too_short", samples=n, reason=f"{n} samples, need {config.needed_samples}")
    times = [t for t, _ in series]
    values = [v for _, v in series]

    for i in range(config.min_baseline, n - config.persist + 1):
        baseline = values[:i]
        median = statistics.median(baseline)
        mad = statistics.median(abs(v - median) for v in baseline)
        scale = max(MAD_TO_SIGMA * mad, config.min_scale_fraction * abs(median), 1e-12)
        threshold = max(config.threshold_mads * scale, config.min_relative_change * abs(median))
        sign = 1 if values[i] >= median else -1
        run = values[i : i + config.persist]
        if not all(_departed(v, median, threshold, sign) for v in run):
            continue
        tail = values[i:]
        sustained = sum(_departed(v, median, threshold, sign) for v in tail) / len(tail)
        if sustained < config.sustained_fraction or not _departed(values[-1], median, threshold, sign):
            continue  # a blip that recovered, not the current incident
        peak = max(tail) if sign > 0 else min(tail)
        reported = min(i + config.safety_lag_samples, n - 1)
        return OnsetResult(
            "detected", times[reported], median, peak, n, f"departed from {median:.4g} to {values[i]:.4g}"
        )
    return OnsetResult("no_departure", samples=n, reason="no sustained departure from the earlier samples")


def _is_change(record: dict[str, Any]) -> bool:
    return record.get("signal_type") == DEPLOY_SIGNAL_TYPE


def _parse_time(record: dict[str, Any]) -> datetime | None:
    raw = record.get("event_time") or record.get("observed_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


_EARLIEST = datetime.min.replace(tzinfo=UTC)


def _time_key(record: dict[str, Any]) -> datetime:
    return _parse_time(record) or _EARLIEST


def condense_evidence(evidence: list[dict[str, Any]], config: OnsetConfig = DEFAULT_CONFIG) -> list[dict[str, Any]]:
    """Replace each metric series with one record whose time is safe for the temporal rule; keep everything else.

    The attribution steps list evidence by position and look at only the first few dozen records, so a range query
    that returns 30 samples per metric would push the changes out of view; and the earliest raw sample is the start
    of the query window, which is not when anything went wrong. Each series of ``config.min_samples`` or more
    samples of one metric becomes one line:

    - a departure from baseline was found: the time of the departure, ``tags["symptom"]`` True;
    - the series is too short to tell (see ``OnsetConfig.needed_samples``): the time of its LATEST sample, which can
      only make the rule rule out fewer changes, ``tags["symptom"]`` True and ``tags["onset_unknown"]`` True;
    - no sustained departure: the latest sample time, ``tags["symptom"]`` False, so it never counts as a symptom.

    Change records and metrics with fewer than ``config.min_samples`` samples, such as the benchmark's point
    observations, are returned unchanged.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in evidence:
        if _is_change(record) or _parse_time(record) is None or not isinstance(record.get("value"), int | float):
            continue
        groups.setdefault((str(record.get("entity")), str(record.get("metric_name"))), []).append(record)
    series_groups = {key: rows for key, rows in groups.items() if len(rows) >= config.min_samples}
    if not series_groups:
        return evidence

    condensed_ids = {id(r) for rows in series_groups.values() for r in rows}
    out = [r for r in evidence if id(r) not in condensed_ids]
    for (entity, metric), rows in series_groups.items():
        points = [(t, float(r["value"])) for r in rows if (t := _parse_time(r)) is not None]
        result = detect_onset(points, config)
        latest = max(rows, key=_time_key)
        latest_moment = (
            latest_time.isoformat() if (latest_time := _parse_time(latest)) else str(latest.get("observed_at"))
        )
        if result.status == "detected" and result.onset is not None:
            moment = result.onset.isoformat()
            description = (
                f"{metric} on {entity} left its baseline of {result.baseline:.4g} at {moment} "
                f"(peak {result.peak:.4g}, estimated from {result.samples} samples)"
            )
            tags: dict[str, Any] = {"symptom": True, "onset_estimated": True, "samples": result.samples}
        elif result.status == "too_short":
            moment = latest_moment
            description = (
                f"{metric} on {entity}: {result.samples} samples, too few to date a departure; latest at {moment}"
            )
            tags = {"symptom": True, "onset_unknown": True, "samples": result.samples}
        else:
            moment = latest_moment
            description = f"{metric} on {entity} showed no sustained departure ({result.samples} samples)"
            tags = {"symptom": False, "samples": result.samples}
        out.append(
            {
                **{k: v for k, v in latest.items() if k not in {"tags", "description"}},
                "observed_at": moment,
                "event_time": moment,
                "description": description,
                "tags": {**(latest.get("tags") or {}), **tags},
            }
        )
    out.sort(key=_time_key)
    return out
