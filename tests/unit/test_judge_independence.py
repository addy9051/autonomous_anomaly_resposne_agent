import pytest

from evals.provider_check import (
    JudgeIndependenceError,
    assert_judge_independent,
    check_judge_independence,
    choose_independent_judge,
    resolve_provider,
)
from shared.config import LLMConfig

AGENTS = {"default": "llama-3.1-8b-instruct", "diagnosis": "llama-3.1-70b-instruct"}


def _cfg(**kw: object) -> LLMConfig:
    return LLMConfig(_env_file=None, **kw)


def test_resolve_provider() -> None:
    assert resolve_provider("Meta/Llama-3.1-70B") == "meta"
    assert resolve_provider("qwen2.5-72b-instruct") == "alibaba"
    assert resolve_provider("mystery-model") is None


def test_same_provider_fails() -> None:
    r = check_judge_independence("llama-3.1-70b-instruct", AGENTS)
    assert not r.ok and "same as agent" in r.reason


def test_different_provider_passes() -> None:
    assert check_judge_independence("qwen2.5-72b-instruct", AGENTS).ok


def test_unknown_judge_or_agent_fails_closed() -> None:
    assert not check_judge_independence("mystery-model", AGENTS).ok
    assert not check_judge_independence("qwen2.5-72b-instruct", {"a": "mystery"}).ok


def test_override_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER_MAP", "mystery=google")
    assert resolve_provider("mystery-model") == "google"


def test_default_config_is_independent() -> None:
    assert_judge_independent(_cfg().judge_model, _cfg())


def test_assert_raises_on_clash() -> None:
    with pytest.raises(JudgeIndependenceError):
        assert_judge_independent("llama-3.1-70b-instruct", _cfg())


def test_non_interactive_blocks_with_guidance() -> None:
    with pytest.raises(JudgeIndependenceError, match="different provider"):
        choose_independent_judge("llama-3.1-70b-instruct", _cfg(), interactive=False)


def test_interactive_prompt_picks_new_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["llama-3.1-8b-instruct", "1"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    picked = choose_independent_judge("llama-3.1-70b-instruct", _cfg(), interactive=True)
    assert resolve_provider(picked) != "meta"
