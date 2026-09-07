from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from cluefin_agent import cli as cli_module


@pytest.fixture
def stubbed_cli(monkeypatch: pytest.MonkeyPatch):
    """CLI 배선만 확인한다. LLM과 ClickHouse는 가짜로 바꾼다."""

    class FakeSettings:
        model = "test-model"

        @classmethod
        def from_env(cls):
            return cls()

        def create_model(self):
            return object()

    translated: dict = {
        "sql": "SELECT 1 AS `값` LIMIT 1",
        "explanation": "한 줄 설명",
        "notes": ["최근 24개월"],
        "status": "validated",
        "error": "",
        "attempts": 1,
        "model": "test-model",
    }
    calls: dict = {}

    monkeypatch.setattr(cli_module, "PatModelSettings", FakeSettings)
    monkeypatch.setattr(cli_module, "ClickHouseStore", lambda: type("S", (), {"client": lambda self: "client"})())
    monkeypatch.setattr(
        cli_module,
        "translate_question",
        lambda **kwargs: (calls.update(kwargs), translated)[1],
    )
    monkeypatch.setattr(
        cli_module,
        "run_read_only",
        lambda client, sql, max_rows=20: {
            "columns": ["값"],
            "rows": [[1]],
            "row_count": 1,
            "truncated": False,
            "elapsed_ms": 1.0,
        },
    )
    return calls, translated


def test_nl2sql_prints_sql_and_notes(stubbed_cli) -> None:
    calls, _ = stubbed_cli

    result = CliRunner().invoke(cli_module.cli, ["nl2sql", "수도권", "미분양", "추이"])

    assert result.exit_code == 0
    assert "SELECT 1 AS `값` LIMIT 1" in result.output
    assert "# 한 줄 설명" in result.output
    assert "# 주의: 최근 24개월" in result.output
    # 여러 단어로 준 질문은 한 문장으로 합쳐 넘긴다.
    assert calls["question"] == "수도권 미분양 추이"


def test_nl2sql_run_flag_executes_and_prints_table(stubbed_cli) -> None:
    result = CliRunner().invoke(cli_module.cli, ["nl2sql", "미분양", "--run", "--rows", "5"])

    assert result.exit_code == 0
    assert "값" in result.output
    assert "1행 · 1.0ms" in result.output


def test_nl2sql_json_flag_emits_one_object(stubbed_cli) -> None:
    import json

    result = CliRunner().invoke(cli_module.cli, ["nl2sql", "미분양", "--json"])

    payload = json.loads(result.output)
    assert payload["status"] == "validated"
    assert payload["sql"].startswith("SELECT 1")


def test_nl2sql_run_stops_when_validation_failed(stubbed_cli) -> None:
    _, translated = stubbed_cli
    translated.update(status="invalid", error="Unknown expression identifier `nope`.")

    result = CliRunner().invoke(cli_module.cli, ["nl2sql", "이상한 질문", "--run"])

    assert result.exit_code == 1
    assert "검증 실패" in result.output


def test_cli_group_loads_llm_settings_from_the_env_file(monkeypatch, tmp_path) -> None:
    """CLUEFIN_LLM_* 를 .env에만 적어 둔 경우에도 "설정이 없습니다"가 나오지 않아야 한다."""

    from cluefin_agent.config import PatModelSettings

    (tmp_path / ".env").write_text(
        "CLUEFIN_LLM_PAT=pat\nCLUEFIN_LLM_BASE_URL=https://gateway/v1\nCLUEFIN_LLM_MODEL=model-x\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for name in ("CLUEFIN_LLM_PAT", "CLUEFIN_LLM_BASE_URL", "CLUEFIN_LLM_MODEL", "CLUEFIN_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)
    seen: dict = {}

    @cli_module.cli.command(name="probe-llm")
    def probe_llm() -> None:
        seen["model"] = os.getenv("CLUEFIN_LLM_MODEL")
        seen["settings"] = PatModelSettings.from_env().model

    try:
        result = CliRunner().invoke(cli_module.cli, ["probe-llm"])
    finally:
        cli_module.cli.commands.pop("probe-llm", None)
        for name in ("CLUEFIN_LLM_PAT", "CLUEFIN_LLM_BASE_URL", "CLUEFIN_LLM_MODEL"):
            os.environ.pop(name, None)

    assert result.exit_code == 0, result.output
    assert seen == {"model": "model-x", "settings": "model-x"}
