"""
Layered evals for the SRE agent.

Layer 1 - deterministic: only allowed tools/actions, correct tiers, valid citations.
Layer 2 - guardrails: regex/string detectors for forbidden or unsafe output.
Layer 3 - LLM-as-a-Judge: rubric scoring, gated on agreement with human experts.
"""
