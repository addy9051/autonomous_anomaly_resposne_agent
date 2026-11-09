"""Judge each candidate change separately and take the confidence from token log-probabilities.

Why this exists: asking one three-way question over the whole list ("which change, none, or cannot tell") made a 7B
model use "cannot tell" as its default answer for any uncertainty (docs/pivot/07). This module asks a much smaller
question per candidate, "could THIS change have caused the alert: YES, NO or UNCLEAR", and lets code combine the
answers. The model never decides between candidates and never decides to abstain; the aggregation rules below do.

The confidence is derived from the probability the serving layer assigns to the YES, NO and UNCLEAR tokens, not from
a number the model writes. If the server returns no log-probabilities, the verdict falls back to the word the model
wrote (probability 1.0) and is flagged ``from_logprobs=False`` so a benchmark can refuse to count it.

The thresholds are fixed constants chosen before any measurement (docs/pivot/06). Neither this module nor the
solver mode that uses it is wired into the production diagnosis graph.
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from agents.diagnosis.attribution import candidate_changes, render_evidence
from shared.config import get_settings
from shared.llm import get_chat_model
from shared.pii import sanitize_for_llm
from shared.utils import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger("candidate_judge")

# A change counts as supported when the model puts at least this much probability on YES.
YES_THRESHOLD = 0.5
# With no supported change, any candidate at or above this probability on UNCLEAR makes the answer "cannot tell".
UNCLEAR_THRESHOLD = 0.5
# Two supported changes closer than this are treated as equally plausible, which is "cannot tell".
AMBIGUITY_MARGIN = 0.2
# Probability mass that must sit on YES/NO/UNCLEAR tokens for the log-probabilities to be trusted.
MIN_ANSWER_MASS = 0.5
TOP_LOGPROBS = 10

JUDGE_SYSTEM_PROMPT = "You are a careful SRE. Answer with exactly one word: YES, NO or UNCLEAR."

JUDGE_PROMPT = """Use only the evidence below. Do not invent evidence.

Alert: {alert}

Evidence, cited by id (CHANGE lines are deployments, configuration, limit or flag changes):
{evidence}

Candidate change to judge: {candidate}

Could this specific change have caused the alert, judging only from the evidence?
- YES if the evidence supports it.
- NO if the evidence contradicts it, or the change is on an unrelated service, or its timing does not fit the symptoms.
- UNCLEAR if the evidence cannot tell.
Answer with one word: YES, NO or UNCLEAR."""


@dataclass(frozen=True)
class Verdict:
    """The model's judgement of one candidate change. Probabilities sum to 1."""

    evidence_id: str
    p_yes: float
    p_no: float
    p_unclear: float
    from_logprobs: bool = True
    note: str = ""


@dataclass(frozen=True)
class Judgement:
    """The combined answer: a change, no change, or cannot tell, with a confidence derived from the verdicts."""

    decision: str
    change_id: str | None
    confidence: float
    reason: str
    verdicts: tuple[Verdict, ...] = ()


def classify_token(token: str) -> str | None:
    """Which answer a first token stands for. ``UN`` and ``UNC`` count because UNCLEAR may be split into tokens."""
    word = token.strip().upper()
    if word in {"YES", "Y"}:
        return "yes"
    if word in {"NO", "N"}:
        return "no"
    if len(word) >= 2 and "UNCLEAR".startswith(word):
        return "unclear"
    return None


def probabilities_from_logprobs(content: Sequence[dict[str, Any]] | None) -> tuple[float, float, float, float] | None:
    """(p_yes, p_no, p_unclear, mass) for the first answer token, normalised over the three answers.

    ``mass`` is the share of the model's probability that sat on any of the three, before normalising. None when no
    usable log-probabilities were returned.
    """
    if not content:
        return None
    first = next((entry for entry in content if str(entry.get("token", "")).strip()), None)
    if first is None:
        return None
    alternatives = list(first.get("top_logprobs") or [])
    if not any(a.get("token") == first.get("token") for a in alternatives):
        alternatives.append({"token": first.get("token"), "logprob": first.get("logprob")})
    mass = {"yes": 0.0, "no": 0.0, "unclear": 0.0}
    for alternative in alternatives:
        kind = classify_token(str(alternative.get("token", "")))
        logprob = alternative.get("logprob")
        if kind is not None and isinstance(logprob, int | float) and math.isfinite(logprob):
            mass[kind] += math.exp(logprob)
    total = sum(mass.values())
    if total <= 0:
        return None
    return mass["yes"] / total, mass["no"] / total, mass["unclear"] / total, min(total, 1.0)


_FIRST_WORD = re.compile(r"[A-Za-z]+")


def verdict_from_response(evidence_id: str, content: str, logprobs: Sequence[dict[str, Any]] | None) -> Verdict:
    """A verdict from the model's reply. Log-probabilities when usable; otherwise the written word, flagged."""
    parsed = probabilities_from_logprobs(logprobs)
    if parsed is not None:
        p_yes, p_no, p_unclear, mass = parsed
        if mass >= MIN_ANSWER_MASS:
            return Verdict(evidence_id, p_yes, p_no, p_unclear)
        return Verdict(evidence_id, 0.0, 0.0, 1.0, note="answer was not YES, NO or UNCLEAR")
    match = _FIRST_WORD.search(content or "")
    kind = classify_token(match.group(0)) if match else None
    if kind == "yes":
        return Verdict(evidence_id, 1.0, 0.0, 0.0, from_logprobs=False)
    if kind == "no":
        return Verdict(evidence_id, 0.0, 1.0, 0.0, from_logprobs=False)
    return Verdict(evidence_id, 0.0, 0.0, 1.0, from_logprobs=False, note="no usable answer")


def aggregate(
    verdicts: Sequence[Verdict],
    *,
    has_symptoms: bool,
    missing_sources: Sequence[str],
    fallback_confidence: float,
) -> Judgement:
    """Combine per-candidate verdicts. These rules, not the model, decide between candidates and when to abstain.

    1. No symptom evidence at all: nothing to attribute, so cannot tell.
    2. Two supported changes within ``AMBIGUITY_MARGIN`` of each other: cannot tell.
    3. One clearly leading supported change: name it. Confidence is its P(yes), reduced by the runner-up's P(yes).
    4. No supported change: cannot tell if an evidence source was unavailable (an unseen change cannot be ruled
       out) or if any candidate is probably UNCLEAR; otherwise no change caused it, with confidence equal to the
       weakest P(no) among the candidates.
    5. No candidates to judge: no change, with the diagnosis's own confidence, unless a source was unavailable.
    """
    kept = tuple(verdicts)

    def cannot_tell(reason: str) -> Judgement:
        return Judgement("cannot_tell", None, 0.0, reason, kept)

    if not has_symptoms:
        return cannot_tell("no symptom evidence to attribute")
    supported = sorted((v for v in kept if v.p_yes >= YES_THRESHOLD), key=lambda v: -v.p_yes)
    if len(supported) >= 2 and supported[0].p_yes - supported[1].p_yes < AMBIGUITY_MARGIN:
        return cannot_tell(f"{supported[0].evidence_id} and {supported[1].evidence_id} are equally plausible")
    if supported:
        top = supported[0]
        runner_up = supported[1].p_yes if len(supported) > 1 else 0.0
        confidence = round(top.p_yes * (1.0 - runner_up), 4)
        return Judgement("change", top.evidence_id, confidence, f"{top.evidence_id} judged YES", kept)
    if missing_sources:
        return cannot_tell("an evidence source was unavailable, so an unseen change cannot be ruled out")
    if any(v.p_unclear >= UNCLEAR_THRESHOLD for v in kept):
        return cannot_tell("at least one candidate change could not be judged from the evidence")
    if not kept:
        return Judgement("none", None, round(fallback_confidence, 4), "no candidate change to judge", kept)
    return Judgement("none", None, round(min(v.p_no for v in kept), 4), "every candidate change judged NO", kept)


def _logprobs_content(response: Any) -> list[dict[str, Any]] | None:  # noqa: ANN401 - a langchain message
    metadata = getattr(response, "response_metadata", None) or {}
    logprobs = metadata.get("logprobs")
    content = logprobs.get("content") if isinstance(logprobs, dict) else None
    return content if isinstance(content, list) else None


def build_judge_prompt(
    alert_summary: str, evidence: list[dict[str, Any]], evidence_id: str, record: dict[str, Any]
) -> str:
    text = record.get("description") or record.get("metric_name") or record.get("query_or_ref") or ""
    return JUDGE_PROMPT.format(
        alert=sanitize_for_llm(alert_summary),
        evidence=sanitize_for_llm(render_evidence(evidence)),
        candidate=sanitize_for_llm(f"{evidence_id} {text}"),
    )


def judge_model() -> Any:  # noqa: ANN401 - any langchain chat model
    """The diagnosis model, greedy, asked for one short answer with log-probabilities."""
    model = get_chat_model(model_name=get_settings().llm.diagnosis_agent_model, temperature=0.0, max_tokens=4)
    return model.bind(logprobs=True, top_logprobs=TOP_LOGPROBS)


async def judge_candidate(
    model: Any,  # noqa: ANN401
    alert_summary: str,
    evidence: list[dict[str, Any]],
    evidence_id: str,
    record: dict[str, Any],
) -> Verdict:
    try:
        response = await model.ainvoke(
            [
                SystemMessage(content=JUDGE_SYSTEM_PROMPT),
                HumanMessage(content=build_judge_prompt(alert_summary, evidence, evidence_id, record)),
            ]
        )
    except Exception as exc:  # noqa: BLE001 - a failed judgement must lower confidence, never take the diagnosis down
        logger.warning("candidate_judgement_failed", evidence_id=evidence_id, error=str(exc))
        return Verdict(evidence_id, 0.0, 0.0, 1.0, from_logprobs=False, note=f"call failed: {type(exc).__name__}")
    return verdict_from_response(evidence_id, str(response.content), _logprobs_content(response))


async def judge_candidates(
    alert_summary: str,
    evidence: list[dict[str, Any]],
    exclude: frozenset[str] = frozenset(),
    llm: Any = None,  # noqa: ANN401
) -> list[Verdict]:
    """Judge every candidate change not in ``exclude`` (for example, changes code has already ruled out)."""
    candidates = {eid: record for eid, record in candidate_changes(evidence).items() if eid not in exclude}
    if not candidates:
        return []
    model = llm or judge_model()
    return list(
        await asyncio.gather(
            *(judge_candidate(model, alert_summary, evidence, eid, record) for eid, record in candidates.items())
        )
    )
