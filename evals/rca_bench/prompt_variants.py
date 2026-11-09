"""Synthesis-prompt variants for controlled A/B runs of the benchmark.

A variant is applied only for the duration of a benchmark run (the production prompt in
``agents/diagnosis/prompts.py`` is untouched), so a change can be measured against the baseline on the same suite
before anyone decides to adopt it.

The text is used with ``str.format``, so it must not contain curly braces.

Overfitting warning: ``evidence_discipline`` was written after reading the first benchmark runs, and its rules
mirror failure modes that the synthetic suite exercises. A better score on the suite it was designed against is
optimistic. Judge it on the holdout suite (``synthetic_v0_seed1.jsonl``) that was not looked at while writing it,
and on live fault-injected scenarios once they exist.
"""

from __future__ import annotations

EVIDENCE_DISCIPLINE = """
Rules for choosing the cause and the category:
- A recent change is a suspect only if the evidence ties it to the symptom: it landed before the symptoms began,
  it is on the failing component, or its files or description match the error. A change that landed after the
  symptoms began, or that is unrelated to the failing component, is not the cause.
- If the evidence points to something other than a listed change, such as an upstream dependency that is
  unavailable, a surge in request rate, or resource saturation, name that as the cause, cite those records, set
  the category to external or infrastructure as appropriate, and do not name a change as the cause.
- Use application only for faults in application code or its configuration, database for database faults,
  external for upstream or third-party failures, and infrastructure for capacity, resource limits or node problems.
- If the evidence cannot distinguish between causes (a source is unavailable, or several changes landed together
  and nothing separates them), set the category to unknown, state what evidence is missing, give a low confidence
  and do not guess.
"""

VARIANTS: dict[str, str] = {
    "baseline": "",
    "evidence_discipline": EVIDENCE_DISCIPLINE,
}


def synthesis_prompt_for(variant: str, base_prompt: str) -> str:
    """The full synthesis prompt for a variant. Raises on an unknown name so a typo cannot pass as baseline."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown prompt variant '{variant}'. choose from: {', '.join(sorted(VARIANTS))}")
    extra = VARIANTS[variant]
    return base_prompt if not extra else base_prompt.rstrip() + "\n" + extra
