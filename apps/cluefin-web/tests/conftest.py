from __future__ import annotations

import pytest
from cluefin_agent.config import BASE_URL_ENV_VARS, MODEL_ENV_VARS, PAT_ENV_VARS
from cluefin_store.env import ENV_PATH_VAR


@pytest.fixture(autouse=True)
def isolate_llm_env(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):
    """LLM 설정을 개발자 머신의 `.env`나 셸에서 떼어 놓는다.

    `create_app()`이 저장소 `.env`를 읽으므로, 격리하지 않으면 "설정이 없을 때"를 다루는 테스트가
    로컬에 자격증명이 있는지에 따라 통과·실패한다. 빈 파일을 가리켜 로더가 아무것도 못 찾게 한다.
    """
    empty = tmp_path_factory.mktemp("env") / "empty.env"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv(ENV_PATH_VAR, str(empty))
    for name in (*PAT_ENV_VARS, *BASE_URL_ENV_VARS, *MODEL_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    return empty
