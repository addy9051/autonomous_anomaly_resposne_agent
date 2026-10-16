"""
Deterministic guard: the LLM judge must come from a different provider than every agent model.

A judge from the same model family as the agents shares its blind spots and tends to favour its own
style, which inflates scores. Providers are resolved from the model name with a fixed pattern table
(no LLM involved). A model whose provider cannot be resolved fails the check, because independence
cannot be proven; add it to ``MODEL_PROVIDER_MAP`` (``pattern=provider`` pairs) to declare it.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

from shared.config import LLMConfig

_PATTERNS: list[tuple[str, str]] = [
    (r"llama|codellama", "meta"),
    (r"qwen|qwq", "alibaba"),
    (r"mistral|mixtral|codestral|ministral", "mistral"),
    (r"gemma|gemini", "google"),
    (r"phi-?\d", "microsoft"),
    (r"deepseek", "deepseek"),
    (r"granite", "ibm"),
    (r"command-r|aya", "cohere"),
    (r"yi-", "01-ai"),
    (r"glm|chatglm", "zhipu"),
    (r"nemotron", "nvidia"),
    (r"olmo", "allenai"),
    (r"gpt-oss|gpt-", "openai"),
    (r"claude", "anthropic"),
]

SUGGESTED_JUDGES = ["qwen2.5-72b-instruct", "mistral-large-instruct", "gemma-2-27b-it", "deepseek-v3"]


class JudgeIndependenceError(RuntimeError):
    def __init__(self, message: str, judge_model: str, provider: str | None, agent_providers: dict[str, str | None]):
        super().__init__(message)
        self.judge_model = judge_model
        self.provider = provider
        self.agent_providers = agent_providers


@dataclass(frozen=True)
class IndependenceResult:
    ok: bool
    judge_provider: str | None
    agent_providers: dict[str, str | None]
    reason: str = ""


def _overrides() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in os.environ.get("MODEL_PROVIDER_MAP", "").split(","):
        if "=" in item:
            pattern, provider = item.split("=", 1)
            if pattern.strip() and provider.strip():
                pairs.append((re.escape(pattern.strip().lower()), provider.strip().lower()))
    return pairs


def resolve_provider(model: str) -> str | None:
    name = model.lower().rsplit("/", 1)[-1]
    for pattern, provider in _overrides() + _PATTERNS:
        if re.search(pattern, name):
            return provider
    return None


def agent_models(cfg: LLMConfig) -> dict[str, str]:
    return {
        "default": cfg.default_model,
        "monitoring": cfg.monitoring_agent_model,
        "diagnosis": cfg.diagnosis_agent_model,
        "action": cfg.action_agent_model,
        "feedback": cfg.feedback_agent_model,
    }


def check_judge_independence(judge_model: str, models: dict[str, str]) -> IndependenceResult:
    judge_provider = resolve_provider(judge_model)
    agent_providers = {role: resolve_provider(m) for role, m in models.items()}
    if judge_provider is None:
        return IndependenceResult(False, None, agent_providers, f"cannot determine the provider of judge model '{judge_model}'")
    unknown = sorted(r for r, p in agent_providers.items() if p is None)
    if unknown:
        return IndependenceResult(False, judge_provider, agent_providers, f"cannot determine the provider of agent model(s): {', '.join(unknown)}")
    clashes = sorted(r for r, p in agent_providers.items() if p == judge_provider)
    if clashes:
        return IndependenceResult(
            False, judge_provider, agent_providers, f"judge '{judge_model}' is from provider '{judge_provider}', same as agent(s): {', '.join(clashes)}"
        )
    return IndependenceResult(True, judge_provider, agent_providers)


def assert_judge_independent(judge_model: str, cfg: LLMConfig) -> None:
    result = check_judge_independence(judge_model, agent_models(cfg))
    if not result.ok:
        raise JudgeIndependenceError(result.reason, judge_model, result.judge_provider, result.agent_providers)


def choose_independent_judge(judge_model: str, cfg: LLMConfig, interactive: bool | None = None) -> str:
    """Return a judge model that passes the check, prompting the user to pick another provider if it fails."""
    models = agent_models(cfg)
    result = check_judge_independence(judge_model, models)
    if result.ok:
        return judge_model
    if interactive is None:
        interactive = sys.stdin.isatty()
    used = {p for p in result.agent_providers.values() if p}
    options = [m for m in SUGGESTED_JUDGES if (resolve_provider(m) or "") not in used]
    message = f"Judge independence check failed: {result.reason}."
    if not interactive:
        raise JudgeIndependenceError(
            f"{message} Set JUDGE_MODEL to a model from a different provider (e.g. {', '.join(options)}).",
            judge_model,
            result.judge_provider,
            result.agent_providers,
        )
    print(message)
    while True:
        print("Choose a judge model from a different provider (served by your in-VPC inference server):")
        for i, m in enumerate(options, 1):
            print(f"  {i}. {m} [{resolve_provider(m)}]")
        answer = input("Number or model name (blank to abort): ").strip()
        if not answer:
            raise JudgeIndependenceError(message, judge_model, result.judge_provider, result.agent_providers)
        pick = options[int(answer) - 1] if answer.isdigit() and 0 < int(answer) <= len(options) else answer
        if check_judge_independence(pick, models).ok:
            print(f"Using judge model '{pick}'. Set JUDGE_MODEL={pick} to make this permanent.")
            return pick
        print(f"'{pick}' still fails the independence check; try again.")
