# ruff: noqa: S311
"""Deterministic generator for the synthetic RCA benchmark suite.

Scenarios are modelled on the Online Boutique microservice demo (service names and failure modes) but are
fabricated: they test whether a solver can attribute an incident to a change, name the right category and
abstain when the evidence cannot support a conclusion. They are NOT measurements of real incidents, and a
score on this suite must never be presented as real-world accuracy. Live, fault-injected scenarios use the
same schema with ``source="live"`` and are reported separately.

The suite is frozen to JSONL (``suites/synthetic_v0.jsonl``) so results are comparable across code versions.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence

from evals.rca_bench.schema import Alert, Category, Change, ChangeKind, GroundTruth, Scenario, Signal

SERVICES = [
    "frontend",
    "checkout",
    "cart",
    "payment",
    "shipping",
    "currency",
    "recommendation",
    "productcatalog",
    "email",
    "ad",
]
DEPENDENCIES = ["payment-provider", "email-gateway", "currency-api"]

BENIGN_SUMMARIES = [
    "Update dashboard labels",
    "Bump base image patch version",
    "Adjust log formatting",
    "Rename internal metric",
    "Update README and docs",
    "Rotate service account annotation",
]
DEPLOY_SUMMARIES = [
    "Refactor retry logic in request handler",
    "Bump gRPC client library to next minor",
    "Change item serialization format",
    "Enable new pricing code path",
    "Switch to batched database writes",
]
MODULES = ["handlers", "client", "pricing", "serializer", "session"]


class _Builder:
    """Collects symbolic changes and signals, then assigns time-ordered ids."""

    def __init__(self, scenario_id: str, difficulty: str, alert: Alert) -> None:
        self.scenario_id = scenario_id
        self.difficulty = difficulty
        self.alert = alert
        self._changes: dict[str, Change] = {}
        self._signals: dict[str, Signal] = {}

    def change(
        self,
        key: str,
        kind: ChangeKind,
        service: str,
        summary: str,
        minutes_before: int,
        files: Sequence[str] = (),
    ) -> None:
        self._changes[key] = Change(
            change_id=key,
            evidence_id=key,
            kind=kind,
            service=service,
            summary=summary,
            offset_s=-minutes_before * 60,
            files=list(files),
        )

    def signal(
        self,
        key: str,
        service: str,
        metric: str,
        value: float,
        baseline: float | None = None,
        minutes_before: int = 0,
        detail: str = "",
    ) -> None:
        self._signals[key] = Signal(
            evidence_id=key,
            service=service,
            metric=metric,
            value=value,
            baseline=baseline,
            offset_s=-minutes_before * 60,
            detail=detail,
        )

    def build(
        self,
        *,
        culprit: str | None,
        category: Category,
        fault_class: str,
        rationale: str,
        support: Sequence[str] = (),
        answerable: bool = True,
        unavailable: Sequence[str] = (),
    ) -> Scenario:
        changes = sorted(self._changes.values(), key=lambda c: c.offset_s)
        change_ids = {c.change_id: f"chg-{i + 1:03d}" for i, c in enumerate(changes)}
        items: list[tuple[int, int, str]] = [(c.offset_s, 0, c.evidence_id) for c in changes] + [
            (s.offset_s, 1, s.evidence_id) for s in self._signals.values()
        ]
        evidence_ids = {key: f"ev-{i + 1:03d}" for i, (_, _, key) in enumerate(sorted(items))}
        for c in changes:
            c.change_id, c.evidence_id = change_ids[c.change_id], evidence_ids[c.evidence_id]
        signals = sorted(self._signals.values(), key=lambda s: s.offset_s)
        for s in signals:
            s.evidence_id = evidence_ids[s.evidence_id]
        return Scenario(
            scenario_id=self.scenario_id,
            difficulty=self.difficulty,  # type: ignore[arg-type]
            alert=self.alert,
            signals=signals,
            changes=changes,
            unavailable_sources=list(unavailable),
            ground_truth=GroundTruth(
                answerable=answerable,
                culprit_change_id=change_ids[culprit] if culprit else None,
                category=category,
                fault_class=fault_class,
                rationale=rationale,
                supporting_evidence_ids=[evidence_ids[k] for k in support],
            ),
        )


def _others(rng: random.Random, svc: str, n: int) -> list[str]:
    return rng.sample([s for s in SERVICES if s != svc], n)


def _decoys(
    b: _Builder, rng: random.Random, svc: str, n: int, low_min: int, high_min: int, prefix: str = "decoy"
) -> None:
    for i, other in enumerate(_others(rng, svc, n)):
        kind: ChangeKind = rng.choice(["deploy", "config", "feature_flag"])
        b.change(
            f"{prefix}{i}",
            kind,
            other,
            rng.choice(BENIGN_SUMMARIES + DEPLOY_SUMMARIES),
            rng.randint(low_min, high_min),
            files=[f"src/{other}/{rng.choice(MODULES)}.go"],
        )


def _error_signals(b: _Builder, rng: random.Random, svc: str, onset_min: int, frame: str) -> list[str]:
    err = round(rng.uniform(4.0, 12.0), 1)
    p99 = round(rng.uniform(1500, 3200))
    b.signal("err", svc, "error_rate", err, 0.2, onset_min, detail=f"top error frame: {frame}")
    b.signal("lat", svc, "latency_p99", p99, 200, max(onset_min - 1, 0))
    return ["err", "lat"]


def _alert(svc: str, anomaly_type: str, summary: str, severity: str = "high") -> Alert:
    return Alert(service=svc, anomaly_type=anomaly_type, severity=severity, summary=summary)  # type: ignore[arg-type]


def bad_deploy(sid: str, rng: random.Random, i: int, difficulty: str) -> Scenario:
    svc = rng.choice(SERVICES)
    frame = f"src/{svc}/{rng.choice(MODULES)}.go"
    b = _Builder(sid, difficulty, _alert(svc, "error_rate", f"{svc} error rate elevated after recent release"))
    t = rng.randint(10, 25)
    b.change("culprit", "deploy", svc, rng.choice(DEPLOY_SUMMARIES), t, files=[frame])
    _decoys(b, rng, svc, rng.randint(2, 3), 120, 360)
    support = ["culprit", *_error_signals(b, rng, svc, t - 2, frame)]
    return b.build(
        culprit="culprit",
        category="application",
        fault_class="bad_deploy",
        rationale=(
            "Errors began minutes after the only recent change to the affected service; "
            "the error frame is in the changed file."
        ),
        support=support,
    )


def bad_deploy_decoy_later(sid: str, rng: random.Random, i: int, difficulty: str) -> Scenario:
    svc = rng.choice(SERVICES)
    frame = f"src/{svc}/{rng.choice(MODULES)}.go"
    b = _Builder(sid, difficulty, _alert(svc, "error_rate", f"{svc} error rate elevated"))
    t = rng.randint(35, 60)
    b.change("culprit", "deploy", svc, rng.choice(DEPLOY_SUMMARIES), t, files=[frame])
    _decoys(b, rng, svc, rng.randint(2, 3), 3, 12)
    support = ["culprit", *_error_signals(b, rng, svc, t - 3, frame)]
    return b.build(
        culprit="culprit",
        category="application",
        fault_class="bad_deploy_decoy_later",
        rationale=(
            "The culprit is older than several unrelated changes on other services; "
            "the most recent change is not the cause."
        ),
        support=support,
    )


def same_service_later_noop(sid: str, rng: random.Random, i: int, difficulty: str) -> Scenario:
    svc = rng.choice(SERVICES)
    frame = f"src/{svc}/{rng.choice(MODULES)}.go"
    b = _Builder(sid, difficulty, _alert(svc, "error_rate", f"{svc} error rate elevated"))
    t = rng.randint(40, 60)
    b.change("culprit", "deploy", svc, rng.choice(DEPLOY_SUMMARIES), t, files=[frame])
    b.change(
        "noop",
        rng.choice(["config", "feature_flag"]),
        svc,
        rng.choice(BENIGN_SUMMARIES),
        rng.randint(4, 15),
        files=["docs/ops/labels.yaml"],
    )
    _decoys(b, rng, svc, 1, 20, 90)
    support = ["culprit", *_error_signals(b, rng, svc, t - 2, frame)]
    return b.build(
        culprit="culprit",
        category="application",
        fault_class="same_service_later_noop",
        rationale=(
            "A harmless later change on the same service must be ruled out because "
            "the error frame matches only the older deploy's files."
        ),
        support=support,
    )


def resource_limit_change(sid: str, rng: random.Random, i: int, difficulty: str) -> Scenario:
    svc = rng.choice(["cart", "recommendation", "productcatalog", "checkout"])
    b = _Builder(sid, difficulty, _alert(svc, "resource_saturation", f"{svc} pods restarting", "high"))
    t = rng.randint(15, 30)
    old, new = rng.choice([("512Mi", "128Mi"), ("1Gi", "256Mi"), ("768Mi", "192Mi")])
    b.change("culprit", "resource_limit", svc, f"Memory limit {old} -> {new}", t, files=[f"deploy/{svc}.yaml"])
    _decoys(b, rng, svc, 2, 5, 20)
    b.signal("oom", svc, "oom_kills", float(rng.randint(3, 9)), 0.0, t - 2, detail="container terminated: OOMKilled")
    b.signal("mem", svc, "memory_percent", round(rng.uniform(96, 100), 1), 55.0, t - 2)
    return b.build(
        culprit="culprit",
        category="infrastructure",
        fault_class="resource_limit_change",
        rationale="OOM kills began right after the memory limit was reduced on the affected workload.",
        support=["culprit", "oom", "mem"],
    )


def config_change(sid: str, rng: random.Random, i: int, difficulty: str) -> Scenario:
    options: list[tuple[str, Category, str, str]] = [
        ("Downstream timeout 5s -> 200ms", "application", "latency_p99", "timeouts to downstream calls"),
        ("DB connection pool 50 -> 5", "database", "latency_p99", "waiting for a free database connection"),
        ("Retry budget raised 2 -> 10", "application", "error_rate", "retry storm amplifying downstream errors"),
    ]
    summary, category, metric, detail = options[i % len(options)]
    svc = rng.choice(["checkout", "cart", "payment", "frontend"])
    b = _Builder(sid, difficulty, _alert(svc, "latency_spike", f"{svc} {metric} elevated"))
    t = rng.randint(10, 30)
    b.change("culprit", "config", svc, summary, t, files=[f"config/{svc}.yaml"])
    _decoys(b, rng, svc, 2, 40, 200)
    value = round(rng.uniform(1800, 3500)) if metric == "latency_p99" else round(rng.uniform(5, 12), 1)
    b.signal("sig", svc, metric, value, 200 if metric == "latency_p99" else 0.2, t - 1, detail=detail)
    return b.build(
        culprit="culprit",
        category=category,
        fault_class="config_change",
        rationale="The symptom matches the changed setting and starts right after it was applied.",
        support=["culprit", "sig"],
    )


def dependency_outage(sid: str, rng: random.Random, i: int, difficulty: str) -> Scenario:
    svc = rng.choice(["checkout", "payment", "frontend"])
    dep = rng.choice(DEPENDENCIES)
    b = _Builder(sid, difficulty, _alert(svc, "error_rate", f"{svc} error rate elevated"))
    b.change("decoy_recent", "deploy", svc, rng.choice(BENIGN_SUMMARIES), 20, files=[f"src/{svc}/ui.go"])
    _decoys(b, rng, svc, 2, 60, 240)
    b.signal(
        "err",
        svc,
        "error_rate",
        round(rng.uniform(6, 14), 1),
        0.2,
        45,
        detail=f"upstream {dep} returning 503 since T-45m",
    )
    b.signal("dep", dep, "availability", round(rng.uniform(0.3, 0.6), 2), 0.999, 45)
    return b.build(
        culprit=None,
        category="external",
        fault_class="dependency_outage",
        rationale=(
            "Errors started before the most recent change and the upstream dependency is unavailable; "
            "no listed change is responsible."
        ),
        support=["err", "dep"],
    )


def traffic_surge(sid: str, rng: random.Random, i: int, difficulty: str) -> Scenario:
    svc = rng.choice(["frontend", "cart", "productcatalog"])
    b = _Builder(sid, difficulty, _alert(svc, "resource_saturation", f"{svc} saturated, latency elevated", "high"))
    b.change(
        "decoy_recent", "deploy", svc, rng.choice(DEPLOY_SUMMARIES), 25, files=[f"src/{svc}/{rng.choice(MODULES)}.go"]
    )
    _decoys(b, rng, svc, 2, 60, 240)
    mult = round(rng.uniform(4.0, 6.0), 1)
    b.signal(
        "rps",
        svc,
        "request_rate",
        round(mult * 100),
        100,
        35,
        detail=f"request rate {mult}x baseline, rising since T-35m",
    )
    b.signal("cpu", svc, "cpu_percent", round(rng.uniform(94, 100), 1), 45.0, 30)
    return b.build(
        culprit=None,
        category="infrastructure",
        fault_class="traffic_surge",
        rationale="Load rose before the most recent deploy; capacity saturation, not a change, explains the symptoms.",
        support=["rps", "cpu"],
    )


def insufficient_evidence(sid: str, rng: random.Random, i: int, difficulty: str) -> Scenario:
    svc = rng.choice(SERVICES)
    b = _Builder(sid, difficulty, _alert(svc, "error_rate", f"{svc} error rate elevated"))
    if i % 2 == 0:
        b.change("c1", "deploy", svc, rng.choice(DEPLOY_SUMMARIES), 15, files=[f"src/{svc}/{rng.choice(MODULES)}.go"])
        _decoys(b, rng, svc, 2, 30, 120)
        return b.build(
            culprit=None,
            category="unknown",
            fault_class="insufficient_evidence",
            rationale="Telemetry is unavailable, so no conclusion can be justified.",
            answerable=False,
            unavailable=["telemetry"],
        )
    t = rng.randint(20, 30)
    b.change("c1", "deploy", svc, rng.choice(DEPLOY_SUMMARIES), t, files=[f"src/{svc}/{rng.choice(MODULES)}.go"])
    b.change("c2", "config", svc, "Adjust request limits", t, files=[f"config/{svc}.yaml"])
    b.signal("err", svc, "error_rate", round(rng.uniform(4, 9), 1), 0.2, t - 2)
    return b.build(
        culprit=None,
        category="unknown",
        fault_class="insufficient_evidence",
        rationale="Two changes landed together and no signal distinguishes them.",
        support=["err"],
        answerable=False,
    )


FaultBuilder = Callable[[str, random.Random, int, str], Scenario]

FAULT_CLASSES: list[tuple[str, FaultBuilder, str]] = [
    ("bad_deploy", bad_deploy, "easy"),
    ("bad_deploy_decoy_later", bad_deploy_decoy_later, "medium"),
    ("same_service_later_noop", same_service_later_noop, "hard"),
    ("resource_limit_change", resource_limit_change, "medium"),
    ("config_change", config_change, "medium"),
    ("dependency_outage", dependency_outage, "hard"),
    ("traffic_surge", traffic_surge, "medium"),
    ("insufficient_evidence", insufficient_evidence, "hard"),
]


def generate_suite(seed: int = 0, per_class: int = 5) -> list[Scenario]:
    """Build the suite. The same seed and per_class always produce identical scenarios."""
    scenarios: list[Scenario] = []
    for fault_class, builder, difficulty in FAULT_CLASSES:
        for i in range(per_class):
            sid = f"{fault_class}-{i + 1:02d}"
            rng = random.Random(f"{seed}:{sid}")
            scenarios.append(builder(sid, rng, i, difficulty))
    return scenarios
