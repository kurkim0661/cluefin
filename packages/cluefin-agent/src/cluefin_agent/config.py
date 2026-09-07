from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

# 자격증명은 이미 다른 도구가 export해 둔 경우가 많다. cluefin 전용 이름을 먼저 보고,
# 없으면 널리 쓰이는 이름으로 내려간다. 덕분에 설정 없이 `uv run cluefin-web`이 동작한다.
PAT_ENV_VARS = ("CLUEFIN_LLM_PAT", "LLM_API_KEY", "OPENAI_API_KEY")
BASE_URL_ENV_VARS = ("CLUEFIN_LLM_BASE_URL", "LLM_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE")
MODEL_ENV_VARS = ("CLUEFIN_LLM_MODEL", "LLM_MODEL", "OPENAI_MODEL")
SETTING_ENV_VARS = {"pat": PAT_ENV_VARS, "base_url": BASE_URL_ENV_VARS, "model": MODEL_ENV_VARS}


def resolve_llm_setting(names: tuple[str, ...], environ: Mapping[str, str] | None = None) -> tuple[str, str]:
    """후보 이름을 순서대로 훑어 (값, 값을 준 변수명)을 돌려준다. 없으면 ("", "")."""
    env = environ if environ is not None else os.environ
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value, name
    return "", ""


def llm_settings_report(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    """무엇이 채워졌고 무엇이 비었는지 요약한다. 값은 담지 않는다.

    `missing`은 사람이 채워야 할 대표 이름(CLUEFIN_LLM_*)만 담고,
    `sources`는 실제로 어느 변수에서 읽혔는지 알려 준다.
    """
    resolved = {key: resolve_llm_setting(names, environ) for key, names in SETTING_ENV_VARS.items()}
    missing = [names[0] for key, names in SETTING_ENV_VARS.items() if not resolved[key][0]]
    return {
        "configured": not missing,
        "missing": missing,
        "model": resolved["model"][0] or None,
        "sources": {key: source for key, (_, source) in resolved.items() if source},
    }


@dataclass(frozen=True, slots=True)
class PatModelSettings:
    pat: str
    base_url: str
    model: str
    temperature: float = 0.1

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "PatModelSettings":
        values: dict[str, str] = {}
        missing: list[str] = []
        for key, names in SETTING_ENV_VARS.items():
            value, _ = resolve_llm_setting(names, environ)
            values[key] = value
            if not value:
                missing.append(names[0])
        if missing:
            raise RuntimeError(f"Missing LLM settings: {', '.join(missing)}")
        return cls(pat=values["pat"], base_url=values["base_url"].rstrip("/"), model=values["model"])

    def create_model(self):
        from langchain_openai import ChatOpenAI

        # ChatOpenAI sends the supplied PAT as an Authorization: Bearer token.
        # No token value is logged or persisted in report snapshots.
        return ChatOpenAI(
            api_key=self.pat,
            base_url=self.base_url,
            model=self.model,
            temperature=self.temperature,
            max_retries=2,
            timeout=90,
        )
