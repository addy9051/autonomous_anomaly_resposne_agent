"""Change attribution as a narrow decision plus checks that code enforces.

Why this exists: giving a 7B model one large prompt full of rules shifted its behaviour everywhere instead of where
it was needed (see docs/pivot/07). This module instead splits the question the way a responder would:

1. A narrow, constrained question for the model: did one *listed* change cause this, was it something else, or can
   the evidence not tell? The answer is a closed set, validated against the evidence, so it cannot invent a change.
2. A deterministic rule in code: a change that landed after the earliest symptom cannot have caused it.

Neither step is wired into the production diagnosis graph yet. They are measured first as benchmark variants
(``evals/rca_bench``), under the decision rule in docs/pivot/06.

Evidence is the list of evidence dicts the graph already builds (``agents/diagnosis/evidence.py``). The rules rely on
``signal_type`` ("deploy_event" marks a change) and ``event_time``. The onset rule needs the time a metric first went
abnormal. A range query returns every sample in its window, so on raw samples the "earliest symptom" is the start of
the window, which would rule out a real culprit that landed a few minutes before the alert. Run
``onset.condense_evidence`` first (``evidence.collect_symptom_evidence`` does): it replaces each series with one
record at the time the metric left its baseline, or at its latest sample when that cannot be told, which only makes
the rule rule out fewer changes. See docs/pivot/08.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage

from shared.config import get_settings
from shared.llm import get_chat_model
from shared.pii import sanitize_for_llm
from shared.utils import get_logger

logger = get_logger("attribution")

DEPLOY_SIGNAL_TYPE = "deploy_event"
CATEGORIES = ("application", "database", "infrastructure", "external", "network", "security")
Decision = Literal["change", "none", "cannot_tell"]
DECISIONS: tuple[str, ...] = ("change", "none", "cannot_tell")
MAX_EVIDENCE_LINES = 40

ATTRIBUTION_SYSTEM_PROMPT = (
    "You are a careful SRE deciding whether a recent change caused an incident. Answer with JSON only."
)

ATTRIBUTION_PROMPT = """Use only the evidence below. Do not invent evidence.

Alert: {alert}

Evidence, cited by id. Lines tagged CHANGE describe a deployment, configuration, limit or flag change and are the
only candidate changes:
{evidence}

Answer with one JSON object and nothing else. Choose exactly one decision:
- {{"decision": "change", "evidence_id": "E#", "category": "<category>", "support": ["E#"]}} when one listed change
  started the problem.
- {{"decision": "none", "category": "<category>", "support": ["E#"]}} when the symptoms are explained by something
  other than a listed change.
- {{"decision": "cannot_tell", "missing": "<what evidence is needed>"}} when the evidence cannot tell the causes apart.
<category> is one of: application, database, infrastructure, external, network, security.
"""


@dataclass(frozen=True)
class Attribution:
    """The outcome of the narrow decision. ``change_id`` is an evidence id such as "E5", validated as a CHANGE."""

    decision: str
    change_id: str | None = None
    category: str | None = None
    support: tuple[str, ...] = ()
    reason: str = ""
    raw: str = ""

    def with_decision(self, decision: str, reason: str) -> Attribution:
        """A copy with a different decision (for example when code rules the chosen change out)."""
        return Attribution(
            decision=decision,
            change_id=self.change_id if decision == "change" else None,
            category=self.category,
            support=self.support,
            reason=reason,
            raw=self.raw,
        )


def evidence_id(index: int) -> str:
    return f"E{index + 1}"


def index_evidence(evidence: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {evidence_id(i): e for i, e in enumerate(evidence[:MAX_EVIDENCE_LINES])}


def candidate_changes(evidence: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {eid: e for eid, e in index_evidence(evidence).items() if e.get("signal_type") == DEPLOY_SIGNAL_TYPE}


def _event_time(record: dict[str, Any]) -> datetime | None:
    raw = record.get("event_time") or record.get("observed_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _is_symptom(record: dict[str, Any]) -> bool:
    """A non-change record, unless onset detection found that its series never left its baseline."""
    return record.get("signal_type") != DEPLOY_SIGNAL_TYPE and (record.get("tags") or {}).get("symptom") is not False


def has_symptom_evidence(evidence: list[dict[str, Any]]) -> bool:
    """Whether there is anything to attribute: at least one symptom record, whether or not its onset is known."""
    return any(_is_symptom(e) for e in evidence[:MAX_EVIDENCE_LINES])


def symptom_onset(evidence: list[dict[str, Any]]) -> datetime | None:
    """Earliest time of any symptom record, or None when there is none.

    For long metric series this is only meaningful after ``onset.condense_evidence``, which replaces the samples
    with the time the metric left its baseline. On raw samples it would be the start of the query window.
    """
    times = [t for e in evidence[:MAX_EVIDENCE_LINES] if _is_symptom(e) and (t := _event_time(e)) is not None]
    return min(times) if times else None


def rule_out_acausal_changes(evidence: list[dict[str, Any]]) -> dict[str, str]:
    """Changes that landed after the earliest symptom, keyed by evidence id with a reason.

    A change cannot cause a symptom that had already started. Changes older than the onset stay possible: this rule
    only removes candidates, it never proves one.
    """
    onset = symptom_onset(evidence)
    if onset is None:
        return {}
    ruled_out: dict[str, str] = {}
    for eid, record in candidate_changes(evidence).items():
        landed = _event_time(record)
        if landed is not None and landed > onset:
            minutes = round((landed - onset).total_seconds() / 60)
            ruled_out[eid] = f"landed {minutes} min after the earliest symptom, so it cannot have caused it"
    return ruled_out


def render_evidence(evidence: list[dict[str, Any]]) -> str:
    lines = []
    for eid, record in index_evidence(evidence).items():
        tag = "CHANGE" if record.get("signal_type") == DEPLOY_SIGNAL_TYPE else "SIGNAL"
        text = record.get("description") or record.get("metric_name") or record.get("query_or_ref") or ""
        lines.append(f"{eid} [{tag}] {text} @ {record.get('event_time') or record.get('observed_at', '')}")
    return "\n".join(lines) if lines else "No evidence collected."


def build_attribution_prompt(alert_summary: str, evidence: list[dict[str, Any]]) -> str:
    return ATTRIBUTION_PROMPT.format(
        alert=sanitize_for_llm(alert_summary),
        evidence=sanitize_for_llm(render_evidence(evidence)),
    )


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_attribution(content: str, evidence: list[dict[str, Any]]) -> Attribution:
    """Validate the model's answer. Anything malformed, or naming a non-change, becomes cannot_tell."""

    def refuse(reason: str) -> Attribution:
        return Attribution(decision="cannot_tell", reason=reason, raw=content[:300])

    match = _JSON_OBJECT.search(content or "")
    if not match:
        return refuse("no JSON object in the answer")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return refuse("the answer was not valid JSON")
    if not isinstance(data, dict):
        return refuse("the answer was not a JSON object")

    decision = str(data.get("decision", "")).strip().lower()
    if decision not in DECISIONS:
        return refuse(f"unknown decision {decision!r}")

    known = index_evidence(evidence)
    category = str(data.get("category", "")).strip().lower()
    category_value = category if category in CATEGORIES else None
    support = tuple(str(s).strip().upper() for s in (data.get("support") or []) if str(s).strip().upper() in known)

    if decision == "change":
        chosen = str(data.get("evidence_id", "")).strip().upper()
        if chosen not in candidate_changes(evidence):
            return refuse(f"{chosen or 'no id'} is not a listed change")
        return Attribution("change", chosen, category_value, support, raw=content[:300])
    if decision == "none":
        return Attribution("none", None, category_value, support, raw=content[:300])
    return Attribution("cannot_tell", reason=str(data.get("missing", ""))[:200], raw=content[:300])


async def decide_attribution(
    alert_summary: str,
    evidence: list[dict[str, Any]],
    llm: Any = None,  # noqa: ANN401 - any langchain chat model
) -> Attribution:
    """Ask the narrow question. A model failure is returned as cannot_tell, never raised into the pipeline."""
    model = llm or get_chat_model(model_name=get_settings().llm.diagnosis_agent_model, temperature=0.0, max_tokens=200)
    try:
        response = await model.ainvoke(
            [
                SystemMessage(content=ATTRIBUTION_SYSTEM_PROMPT),
                HumanMessage(content=build_attribution_prompt(alert_summary, evidence)),
            ]
        )
    except Exception as exc:  # noqa: BLE001 - attribution must never take the diagnosis down
        logger.warning("attribution_failed", error=str(exc))
        return Attribution(decision="cannot_tell", reason=f"attribution call failed: {type(exc).__name__}")
    return parse_attribution(str(response.content), evidence)


def apply_temporal_rule(attribution: Attribution, evidence: list[dict[str, Any]]) -> Attribution:
    """If the chosen change landed after the symptoms began, the decision becomes "none"."""
    if attribution.decision != "change" or attribution.change_id is None:
        return attribution
    reason = rule_out_acausal_changes(evidence).get(attribution.change_id)
    if reason is None:
        return attribution
    return attribution.with_decision("none", f"{attribution.change_id} ruled out: {reason}")
