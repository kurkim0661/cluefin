"""웹에서 NL→SQL 에이전트를 부르는 얇은 어댑터.

에이전트 자체는 cluefin-agent에 있고, 여기서는 LLM 설정 확인과 오류 마스킹만 한다.
"""

from __future__ import annotations

from typing import Any

from cluefin_web.report_jobs import llm_settings_state, missing_llm_message, safe_error

MAX_QUESTION_CHARS = 500


def translate_question_to_sql(client: Any, question: str, *, schema_context: dict | None = None) -> dict[str, Any]:
    """자연어 질문을 SQL로 바꾼다. LLM 설정이 없으면 ValueError로 400을 만든다."""
    text = (question or "").strip()
    if not text:
        raise ValueError("무엇을 보고 싶은지 한 줄로 적어 주세요.")
    if len(text) > MAX_QUESTION_CHARS:
        raise ValueError(f"질문은 {MAX_QUESTION_CHARS}자 이내로 적어 주세요.")
    state = llm_settings_state()
    if not state["configured"]:
        raise ValueError(missing_llm_message(state["missing"]))

    from cluefin_agent.config import PatModelSettings
    from cluefin_agent.sql_agent import translate_question

    settings = PatModelSettings.from_env()
    try:
        return translate_question(
            client=client,
            model=settings.create_model(),
            model_name=settings.model,
            question=text,
            schema_context=schema_context,
        )
    except Exception as exc:  # PAT가 예외 메시지에 실려 브라우저로 나가는 일을 막는다.
        raise RuntimeError(safe_error(exc)) from exc
