"""Evidence-grounded hypothesis modelling.

The LLM proposes hypotheses and cites evidence by index id (E1, E2, ...). Citations are
resolved against the real evidence timeline; unknown ids are dropped, so a hypothesis can
never be "supported" by evidence that was not collected. Confidence is then capped by what
the evidence can actually justify.
"""

from __future__ import annotations

from typing import Any

from shared.schemas import EvidenceRecord, Hypothesis

BAND_ORDER = ["uncalibrated", "low", "medium", "high"]
LOW_CONFIDENCE_CAP = 0.4
MEDIUM_CONFIDENCE_CAP = 0.7


def evidence_id(index: int) -> str:
    return f"E{index + 1}"


def build_evidence_index(evidence_timeline: list[dict[str, Any]], limit: int = 40) -> str:
    """Render the timeline as citable lines for the synthesis prompt."""
    if not evidence_timeline:
        return "No evidence collected."
    lines = []
    for i, e in enumerate(evidence_timeline[:limit]):
        sim = " [SIMULATED]" if e.get("simulated") else ""
        lines.append(
            f"{evidence_id(i)}: {e.get('source', '?')} {e.get('metric_name') or e.get('query_or_ref', '')}"
            f" = {e.get('value')} @ {e.get('event_time') or e.get('observed_at', '')}{sim}"
        )
    return "\n".join(lines)


def _to_record(e: dict[str, Any]) -> EvidenceRecord:
    return EvidenceRecord(
        source=e.get("source", "telemetry"),
        query_or_ref=e.get("query_or_ref", ""),
        event_time=e.get("event_time"),
        freshness_seconds=e.get("freshness_seconds"),
        metric_name=e.get("metric_name"),
        value=e.get("value"),
        unit=e.get("unit", ""),
        description=e.get("description", ""),
        is_contradictory=False,
        tags={**e.get("tags", {}), "simulated": bool(e.get("simulated", False))},
    )


def _resolve(ids: object, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(ids, list):
        return []
    valid: dict[str, dict[str, Any]] = {}
    for raw in ids:
        key = str(raw).strip().upper()
        if not key.startswith("E") or not key[1:].isdigit():
            continue
        idx = int(key[1:]) - 1
        if 0 <= idx < len(evidence):
            valid[key] = evidence[idx]
    return list(valid.values())


def _is_real(e: dict[str, Any]) -> bool:
    return not e.get("simulated", False)


def _lower_band(band: str, ceiling: str) -> str:
    cur = BAND_ORDER.index(band) if band in BAND_ORDER else 0
    return BAND_ORDER[min(cur, BAND_ORDER.index(ceiling))]


def ground_hypotheses(
    raw_hypotheses: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    missing_sources: list[str],
) -> list[Hypothesis]:
    """Resolve citations and cap each hypothesis' confidence band by its evidence."""
    grounded: list[Hypothesis] = []
    for h in raw_hypotheses or []:
        if not isinstance(h, dict) or not h.get("hypothesis"):
            continue
        support = _resolve(h.get("supporting_evidence_ids"), evidence)
        contra = _resolve(h.get("contradicting_evidence_ids"), evidence)
        real_support = [e for e in support if _is_real(e)]

        band = str(h.get("confidence_band", "medium")).lower()
        if band not in BAND_ORDER:
            band = "uncalibrated"
        if not real_support:
            band = _lower_band(band, "low")
        elif len(real_support) == 1 or contra:
            band = _lower_band(band, "medium")

        missing = list(h.get("missing_evidence") or [])
        if not real_support:
            missing.append("no authoritative (non-simulated) evidence cited")
        missing.extend(f"source unavailable: {s}" for s in missing_sources if s not in missing)

        contra_records = [_to_record(e).model_copy(update={"is_contradictory": True}) for e in contra]
        grounded.append(
            Hypothesis(
                hypothesis=str(h["hypothesis"]),
                affected_scope=list(h.get("affected_scope") or []),
                supporting_evidence=[_to_record(e) for e in support],
                contradicting_evidence=contra_records,
                missing_evidence=missing,
                confidence_band=band,
                next_safe_query=str(h.get("next_safe_query", "")),
            )
        )
    # Best-supported first
    grounded.sort(key=lambda x: (len(x.supporting_evidence) - len(x.contradicting_evidence)), reverse=True)
    return grounded


def cap_overall_confidence(
    confidence: float,
    band: str,
    hypotheses: list[Hypothesis],
    evidence: list[dict[str, Any]],
) -> tuple[float, str]:
    """Never report more confidence than the evidence supports."""
    if not any(_is_real(e) for e in evidence):
        return min(confidence, LOW_CONFIDENCE_CAP), _lower_band(band, "low")
    top = hypotheses[0] if hypotheses else None
    if top is None or not top.supporting_evidence:
        return min(confidence, LOW_CONFIDENCE_CAP), _lower_band(band, "low")
    if top.confidence_band in ("low", "uncalibrated"):
        return min(confidence, LOW_CONFIDENCE_CAP), _lower_band(band, "low")
    if top.confidence_band == "medium":
        return min(confidence, MEDIUM_CONFIDENCE_CAP), _lower_band(band, "medium")
    return confidence, band
