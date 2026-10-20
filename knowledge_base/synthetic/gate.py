"""Deterministic quality gate for generated runbooks. No LLM, fully repeatable."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from agents.action.tiers import ACTION_TIERS, classify_action
from knowledge_base.retrieval.bm25 import tokenize

REQUIRED_KEYS = ("title", "symptoms", "diagnosis_steps", "recommended_actions", "verification", "rollback")

DESTRUCTIVE = re.compile(
    r"drop\s+(table|database|schema)|truncate\s+table|delete\s+from\s+\w+\s*(;|$)|rm\s+-rf|mkfs|"
    r"iptables\s+-F|chmod\s+(-R\s+)?777|kubectl\s+delete\s+(namespace|ns|node)|--force\s+--grace-period=0|"
    r"flushall|flushdb|dd\s+if=",
    re.IGNORECASE,
)
INJECTION = re.compile(
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions|disregard\s+(the\s+)?(system|previous)|"
    r"you\s+are\s+now|system\s+prompt|do\s+not\s+tell\s+the\s+(user|sre|operator)|new\s+instructions\s*:",
    re.IGNORECASE,
)
SECRETS = re.compile(
    r"AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"[\w.+-]+@[\w-]+\.[\w.]+|\b\d{3}-\d{2}-\d{4}\b|password\s*[=:]\s*\S+",
    re.IGNORECASE,
)


@dataclass
class GateResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def _as_list(v: Any) -> list[str]:  # noqa: ANN401
    return [str(x) for x in v] if isinstance(v, list) else [str(v or "")]


def runbook_text(rb: dict[str, Any]) -> str:
    parts = [str(rb.get("title", ""))]
    for key in ("symptoms", "diagnosis_steps", "verification", "rollback"):
        parts.extend(_as_list(rb.get(key)))
    actions = rb.get("recommended_actions")
    if isinstance(actions, list):
        parts.extend(str(a.get("rationale", "")) for a in actions if isinstance(a, dict))
    return "\n".join(parts)


def check_runbook(rb: Any, stack: str, failure_mode: str) -> GateResult:  # noqa: ANN401, C901
    """Schema, action registry, safety, secrets and topical grounding."""
    if not isinstance(rb, dict):
        return GateResult(False, ["not_an_object"])
    missing = [k for k in REQUIRED_KEYS if not rb.get(k)]
    if missing:
        return GateResult(False, [f"missing:{','.join(missing)}"])

    reasons: list[str] = []
    steps = rb["diagnosis_steps"]
    if not isinstance(steps, list) or not 3 <= len(steps) <= 10:
        reasons.append("bad_steps")
    if not isinstance(rb["symptoms"], list):
        reasons.append("bad_symptoms")

    actions = rb["recommended_actions"]
    if not isinstance(actions, list) or not 1 <= len(actions) <= 4:
        reasons.append("bad_actions")
    else:
        for a in actions:
            name = a.get("action") if isinstance(a, dict) else None
            if name not in ACTION_TIERS:
                reasons.append(f"unknown_action:{name}")

    text = runbook_text(rb)
    if DESTRUCTIVE.search(text):
        reasons.append("destructive_command")
    if INJECTION.search(text):
        reasons.append("injection_pattern")
    if SECRETS.search(text):
        reasons.append("secret_or_pii")
    if not 3 <= len(str(rb["title"])) <= 120:
        reasons.append("bad_title")
    if not 400 <= len(text) <= 6000:
        reasons.append("bad_length")

    grounded = set(tokenize(str(rb["title"]) + " " + " ".join(_as_list(rb["symptoms"]))))
    if not (set(tokenize(stack)) | set(tokenize(failure_mode))) & grounded:
        reasons.append("off_topic")
    return GateResult(not reasons, reasons)


def render_markdown(rb: dict[str, Any]) -> str:
    lines = [f"# {rb['title']}", "", "## Symptoms", *[f"- {s}" for s in _as_list(rb["symptoms"])], ""]
    lines.append("## Investigation Steps")
    lines += [f"### Step {i}: {s}" for i, s in enumerate(_as_list(rb["diagnosis_steps"]), 1)]
    lines += ["", "## Recommended Actions"]
    for a in rb["recommended_actions"]:
        lines.append(f"- `{a['action']}` (tier {int(classify_action(a['action']))}): {a.get('rationale', '')}")
    lines += ["", "## Verification", *[f"- {v}" for v in _as_list(rb["verification"])]]
    lines += ["", "## Rollback", *[f"- {v}" for v in _as_list(rb["rollback"])]]
    return "\n".join(lines)


def _shingles(text: str, n: int = 5) -> set[int]:
    toks = tokenize(text)
    if len(toks) < n:
        return {int(hashlib.md5(" ".join(toks).encode(), usedforsecurity=False).hexdigest()[:12], 16)}
    return {
        int(hashlib.md5(" ".join(toks[i : i + n]).encode(), usedforsecurity=False).hexdigest()[:12], 16)
        for i in range(len(toks) - n + 1)
    }


def copied_fraction(generated: str, source: str) -> float:
    """Share of the generated text's 5-word shingles that appear verbatim in the grounding source."""
    g = _shingles(generated)
    return len(g & _shingles(source)) / len(g) if g else 0.0


class Deduper:
    """Near-duplicate filter on 5-shingle Jaccard. O(n^2): swap for MinHash-LSH past ~50k docs."""

    def __init__(self, threshold: float = 0.6) -> None:
        self.threshold = threshold
        self._seen: list[set[int]] = []

    def is_duplicate(self, text: str) -> bool:
        s = _shingles(text)
        for other in self._seen:
            union = len(s | other)
            if union and len(s & other) / union >= self.threshold:
                return True
        self._seen.append(s)
        return False
