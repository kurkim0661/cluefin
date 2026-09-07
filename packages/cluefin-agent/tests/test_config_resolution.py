from __future__ import annotations

import pytest

from cluefin_agent.config import PatModelSettings, llm_settings_report, resolve_llm_setting
from cluefin_agent.gateway import gateway_hint

GATEWAY = {
    "CLUEFIN_LLM_PAT": "pat",
    "CLUEFIN_LLM_BASE_URL": "https://gateway/v1",
    "CLUEFIN_LLM_MODEL": "model-x",
}


def test_cluefin_names_win_over_generic_ones() -> None:
    environ = {**GATEWAY, "OPENAI_API_KEY": "other", "OPENAI_MODEL": "other-model"}

    report = llm_settings_report(environ)

    assert report["configured"] is True
    assert report["model"] == "model-x"
    assert report["sources"] == {
        "pat": "CLUEFIN_LLM_PAT",
        "base_url": "CLUEFIN_LLM_BASE_URL",
        "model": "CLUEFIN_LLM_MODEL",
    }


def test_generic_names_are_accepted_when_cluefin_names_are_absent() -> None:
    environ = {"LLM_API_KEY": "pat", "OPENAI_BASE_URL": "https://gateway/v1", "OPENAI_MODEL": "gpt-5.4-mini"}

    report = llm_settings_report(environ)
    settings = PatModelSettings.from_env(environ)

    assert report["configured"] is True
    assert report["sources"]["pat"] == "LLM_API_KEY"
    assert settings.model == "gpt-5.4-mini"
    assert settings.base_url == "https://gateway/v1"


def test_missing_report_names_the_primary_variables() -> None:
    report = llm_settings_report({"OPENAI_API_KEY": "pat"})

    # 사용자에게는 대표 이름만 안내한다. 별칭을 다 나열하면 무엇을 채워야 할지 흐려진다.
    assert report["missing"] == ["CLUEFIN_LLM_BASE_URL", "CLUEFIN_LLM_MODEL"]
    assert report["configured"] is False
    assert report["sources"] == {"pat": "OPENAI_API_KEY"}


def test_blank_and_whitespace_values_count_as_missing() -> None:
    environ = {"CLUEFIN_LLM_PAT": "   ", "CLUEFIN_LLM_BASE_URL": "", "CLUEFIN_LLM_MODEL": "m"}

    assert llm_settings_report(environ)["missing"] == ["CLUEFIN_LLM_PAT", "CLUEFIN_LLM_BASE_URL"]


def test_base_url_trailing_slash_is_trimmed() -> None:
    settings = PatModelSettings.from_env({**GATEWAY, "CLUEFIN_LLM_BASE_URL": "https://gateway/v1/"})

    assert settings.base_url == "https://gateway/v1"


def test_from_env_raises_naming_every_missing_setting() -> None:
    with pytest.raises(RuntimeError) as error:
        PatModelSettings.from_env({})

    message = str(error.value)
    for name in ("CLUEFIN_LLM_PAT", "CLUEFIN_LLM_BASE_URL", "CLUEFIN_LLM_MODEL"):
        assert name in message


def test_resolve_returns_the_first_populated_name() -> None:
    assert resolve_llm_setting(("A", "B"), {"B": "second"}) == ("second", "B")
    assert resolve_llm_setting(("A", "B"), {}) == ("", "")


@pytest.mark.parametrize(
    ("raw", "expected_hint"),
    [
        ("HTTP 429 GenAI Proxy spending limit exceeded", "사용량 한도"),
        ("OpenAIRateLimitError: rate limit reached", "사용량 한도"),
        ("HTTP 401 Unauthorized", "인증을 거부"),
        ("OpenAIConnectionError: Connection error.", "연결하지 못했습니다"),
        ("APITimeoutError: request timed out", "연결하지 못했습니다"),
    ],
)
def test_gateway_hint_explains_what_to_do(raw: str, expected_hint: str) -> None:
    hinted = gateway_hint(raw)

    assert expected_hint in hinted
    # 원문은 디버깅에 필요하므로 버리지 않는다.
    assert raw in hinted


def test_gateway_hint_passes_unknown_errors_through() -> None:
    assert gateway_hint("ValueError: something else") == "ValueError: something else"
