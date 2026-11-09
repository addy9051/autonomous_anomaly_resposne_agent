# 08. Onset detection: making the temporal rule safe on real telemetry

The temporal rule ("a change that landed after the earliest symptom cannot be the cause") lifted top-1 by about 0.19 in the benchmark (doc 07). It was never run on anything but the benchmark's own scenarios, where each signal is one point placed at the symptom start. This document covers what it takes to run it on real telemetry, what was built, and what is still not done.

## The problem

A range query returns every sample in its window, normal ones included. On raw samples the "earliest symptom" is therefore the start of the query window, not when the metric first went abnormal. Fed that, the rule rules out any change that landed after the window began, which includes a real culprit that landed a few minutes before the alert. A unit test (`test_raw_samples_would_rule_out_a_real_culprit_but_condensed_evidence_does_not`) reproduces it.

The direction of the error matters:

- An onset estimate that is too **late** only makes the rule rule out fewer changes. It loses benefit, never correctness.
- An estimate that is too **early** can rule out the true culprit.

So the detector leans late, and says nothing when it cannot tell. In that case the rule does not fire.

## What was built

| Piece | Where | What it does |
|---|---|---|
| Detector | `agents/diagnosis/onset.py` `detect_onset` | The first sample of a sustained departure from the baseline before it, using a robust (median and MAD) threshold. Reports `detected`, `no_departure` or `too_short`. |
| Condensing | `onset.condense_evidence` | Replaces each metric series with one record. Departure found: at the departure time. Too short to tell: at the **latest** sample, flagged `onset_unknown`. No departure: flagged `symptom: False`, so it never counts as a symptom start. Changes and single point observations are untouched (checked on all three benchmark suites). |
| Rule input | `attribution.symptom_onset`, `has_symptom_evidence` | The earliest symptom ignores `symptom: False` records. "Is there anything to attribute" is now separate from "is the onset known", which the judge's abstention rule had conflated. |
| Data path | `evidence.collect_symptom_evidence` | A 5400 s lookback (instead of 900 s) so there are baseline samples before the symptom, then condenses. Not called by the diagnosis graph yet. |
| Benchmark | `evals/rca_bench/agent_solver.py` | `_visible_evidence` condenses (a no-op on the benchmark's point observations), so the existing results are unchanged. |
| Validation | `evals/rca_bench/onset_validation.py` | Rebuilds each scenario's signals as noisy series and checks the production path against the known onset. |

Detector settings (all in `OnsetConfig`, chosen to trade missed onsets for no early ones): at least 12 baseline samples; a departure of at least 6 robust standard deviations and at least 50% of the baseline level; 3 consecutive departed samples; at least 90% of the samples from the onset to the end still departed (so a spike that recovered is not the current incident); the reported time is one sample after the first departed one (a lone outlier just before the real departure would otherwise make the estimate one sample early).

## Validation

Each of the 111 scenarios with signals (three suites) is rebuilt as a series with Gaussian or spiky noise (2% single-sample outliers of 10 standard deviations), a step or an 8-sample ramp, at three noise levels (5%, 15%, 30% of the level), over lookback windows of 900, 3600 and 5400 seconds. 10 noise draws each, then a second run on 30 draws that were never used while choosing the settings, plus 500 and 1000 incident-free series per condition.

| Lookback | Onset has a baseline in the window | Onset dated | Rule's ruling-out kept | Early estimates | True culprit ruled out | False alarms |
|---|---|---|---|---|---|---|
| 900 s (today's evidence window) | 2% | 6-8% | 0% | 0 | 0 | 0 |
| 3600 s | 63% | 82-91% | 67-81% | 0 | 0 | 0 |
| 5400 s | 95% | 92-100% | 79-95% | 0 | 0 | 0 |

"Rule's ruling-out kept" is the share of the changes the rule rules out with the true onset that it still rules out with the estimated one. The ranges cover the noise level, step or ramp, and Gaussian or spiky noise. The ideal rule, with the true onset, is what produced the benchmark's gain. This share is a proxy, not a measured top-1: at 5400 s the gain would shrink by roughly 5% to 20% on series like these, and at the 900 s window the collector uses today the rule would do nothing. Re-running the benchmark with series evidence would measure it.

How the settings were chosen, and a correction: early estimates were first caused by a lone spike before the real departure (fixed by the one-sample lag), then by small-sample noise estimates (fixed by 12 baseline samples and the 90% sustained rule). I fixed a selection criterion first (zero culprits ruled out), then changed it to zero early estimates, because the first depends on the generator putting culprits two minutes before the onset, and a real culprit landing right at the onset would be ruled out by any early estimate. The stricter criterion costs recall (about 0.87 to 0.81).

## Limits

- **Synthetic only.** The noise is Gaussian plus rare single-sample outliers. Real telemetry has seasonality, bursts, gaps, gradual drifts and metrics that were already abnormal. Zero early estimates here is evidence about this noise model, not a guarantee. A real validation needs recorded incident series.
- **Adapters.** The Prometheus adapter returns the whole series, so this works with it. The Datadog adapter returns the latest value and a mean as one record, and the Splunk adapter returns aggregates. For those the onset stays unknown, the rule rules nothing out, and there is no benefit (and no harm). Using them needs the adapters to return the series.
- **Window.** The current evidence window is 900 s. At that length the rule does nothing here. The scenarios' onsets are a median of 35 minutes before the alert, which is probably longer than real alert latencies; if real onsets are close to the alert, a shorter window would be enough, and the benefit would be larger.
- **Which series.** The earliest departure across all collected series sets the onset. An earlier, unrelated departure on a dependency would move it earlier. The benchmark does not exercise this.
- **Not wired in.** Nothing in the production diagnosis graph calls the attribution steps, the judge or `collect_symptom_evidence` yet. The temporal rule is not applied to real incidents today.
- **Long evidence lists.** The attribution steps only show the first 40 evidence records. With raw series that would hide the changes; condensing fixes it for them, but the diagnosis graph's own prompt still receives every sample.

## Reproduce

```powershell
.venv\Scripts\python.exe -m evals.rca_bench.onset_validation                      # the grid above (about 3 minutes)
.venv\Scripts\python.exe -m evals.rca_bench.onset_validation --windows 3600 5400 --seeds 30 --seed-offset 1000 --null-series 1000   # unseen noise draws
.venv\Scripts\python.exe -m pytest tests/unit/test_onset.py -q
```

## What this changes for the next steps

The temporal rule and `judge_temporal` are now runnable on a Prometheus-backed deployment, provided the evidence is collected with `collect_symptom_evidence`. The remaining work is: wire it into the diagnosis path behind a setting, record real incident series to replace the synthetic validation, and give the Datadog and Splunk adapters a series to date.
