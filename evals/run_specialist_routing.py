"""Run the configured supervisor over the web-researched routing benchmark."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agents.diagnosis.prompts import SUPERVISOR_PROMPT
from shared.config import get_settings
from shared.llm import get_chat_model
from shared.schemas import AnomalyEvent, AnomalyType, MetricsSnapshot, Severity

DATA_DIR = Path(__file__).parent / "data"
DEFAULT_CASES = DATA_DIR / "golden_cases.json"
DEFAULT_OUT = Path(__file__).parent / "reports" / "specialist_routing_predictions.json"
EXPERT_KEYS = frozenset({"database_expert", "network_expert", "security_expert", "application_expert"})
SERVICE_SUFFIXES = (
    "service",
    "svc",
    "api",
    "db",
    "gateway",
    "cache",
    "store",
    "worker",
    "proxy",
    "frontend",
    "provider",
    "cluster",
)
_SERVICE_PATTERN = re.compile(r"\b[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*-(?:" + "|".join(SERVICE_SUFFIXES) + r")\b")
_METRIC_FIELDS = {
    "latency_p50": "p50_latency_ms",
    "latency_p95": "p95_latency_ms",
    "latency_p99": "p99_latency_ms",
    "p50_latency_ms": "p50_latency_ms",
    "p95_latency_ms": "p95_latency_ms",
    "p99_latency_ms": "p99_latency_ms",
    "error_rate": "error_rate",
    "cpu_percent": "cpu_percent",
    "memory_percent": "memory_percent",
}


def _severity(issue: str) -> Severity:
    text = issue.lower()
    if any(signal in text for signal in ("unreachable", "notready", "unavailable", "critical")):
        return Severity.CRITICAL
    return Severity.HIGH


def _anomaly_type(issue: str, evidence: list[dict[str, Any]]) -> AnomalyType:
    text = issue.lower()
    metric_names = " ".join(str(item.get("metric", "")) for item in evidence).lower()
    if any(signal in text for signal in ("injection", "401", "authentication", "abuse", "single client ip")):
        return AnomalyType.SECURITY
    if any(signal in text for signal in ("surge", "backlog", "traffic", "429")):
        return AnomalyType.VOLUME_ANOMALY
    if any(signal in text + metric_names for signal in ("latency", "timeout", "slow", "timed out")):
        return AnomalyType.LATENCY_SPIKE
    if any(signal in text + metric_names for signal in ("cpu", "memory", "disk", "connection", "pressure")):
        return AnomalyType.RESOURCE_SATURATION
    if any(signal in text + metric_names for signal in ("error", "failure", "5xx", "502", "503")):
        return AnomalyType.ERROR_RATE
    return AnomalyType.ERROR_RATE


def build_routing_event(case: dict[str, Any]) -> AnomalyEvent:
    """Adapt the older golden-case shape to the event the production supervisor receives."""
    issue = case["issue"]
    evidence = case.get("evidence", [])
    metrics: dict[str, Any] = {}
    custom_metrics: dict[str, float] = {}
    for item in evidence:
        value = item.get("value")
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        field = _METRIC_FIELDS.get(str(item.get("metric", "")).lower())
        if field:
            metrics[field] = float(value)
        else:
            custom_metrics[str(item.get("metric", "unknown"))] = float(value)
    metrics["custom_metrics"] = custom_metrics
    metrics_snapshot = MetricsSnapshot.model_validate(metrics)
    services = sorted(set(_SERVICE_PATTERN.findall(issue)))
    return AnomalyEvent(
        event_id=case["id"],
        severity=_severity(issue),
        affected_services=services,
        anomaly_type=_anomaly_type(issue, evidence),
        metrics_snapshot=metrics_snapshot,
        reasoning=issue,
        confidence=0.8,
        raw_event={"issue": issue, "evidence": evidence},
    )


def _parse_dispatched_experts(raw_response: str) -> tuple[list[str], str | None]:
    """Mirror the supervisor's JSON parsing and application-expert fallback."""
    content = raw_response
    if "```json" in content:
        content = content.split("```json", 1)[1].split("```", 1)[0]
    try:
        expert_keys = json.loads(content)
        if not isinstance(expert_keys, list):
            expert_keys = ["application_expert"]
            fallback_reason = "response_not_list"
        else:
            fallback_reason = None
    except (json.JSONDecodeError, IndexError):
        expert_keys = ["application_expert"]
        fallback_reason = "invalid_json"

    try:
        dispatched = [key for key in expert_keys if key in EXPERT_KEYS]
    except TypeError:
        return ["application_expert"], "invalid_role_value"
    if not dispatched:
        return ["application_expert"], fallback_reason or "no_dispatchable_expert"
    return sorted(set(dispatched)), fallback_reason


async def run(cases: list[dict[str, Any]], concurrency: int = 4) -> dict[str, Any]:
    config = get_settings().llm
    llm = get_chat_model(model_name=config.diagnosis_agent_model, temperature=0, max_tokens=100)
    semaphore = asyncio.Semaphore(concurrency)

    async def run_case(case: dict[str, Any]) -> dict[str, Any]:
        event = build_routing_event(case)
        async with semaphore:
            response = await llm.ainvoke(
                [
                    SystemMessage(content=SUPERVISOR_PROMPT),
                    HumanMessage(content=f"Triage this incident:\n{event.model_dump_json(indent=2)}"),
                ]
            )
        raw = str(response.content)
        dispatched, fallback_reason = _parse_dispatched_experts(raw)
        return {
            "case_id": case["id"],
            "predicted_experts": dispatched,
            "raw_response": raw,
            "fallback_reason": fallback_reason,
        }

    predictions = await asyncio.gather(*(run_case(case) for case in cases))
    case_payload = json.dumps(cases, sort_keys=True, separators=(",", ":"))
    return {
        "model": config.diagnosis_agent_model,
        "temperature": 0,
        "prompt": "SUPERVISOR_PROMPT",
        "prompt_sha256": hashlib.sha256(SUPERVISOR_PROMPT.encode("utf-8")).hexdigest(),
        "cases_sha256": hashlib.sha256(case_payload.encode("utf-8")).hexdigest(),
        "input_adapter": (
            "Each golden issue/evidence pair was normalized to the production AnomalyEvent schema. "
            "No agent_output, expected violations, or labels were included."
        ),
        "predictions": predictions,
    }


async def _main(args: argparse.Namespace) -> None:
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    payload = await run(cases, args.concurrency)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(payload['predictions'])} supervisor predictions from {payload['model']} to {args.out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
