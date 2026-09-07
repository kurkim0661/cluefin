"""자연어 질문을 ClickHouse SQL로 옮기는 LangGraph 에이전트.

일일 리포트 그래프와 같은 배선(PatModelSettings → ChatOpenAI)을 쓰고,
검증은 사람이 아니라 서버가 한다. 생성 → 가드 검사 → EXPLAIN → 실패하면 오류를 되먹여 재시도.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol, TypedDict

from cluefin_store.sql_console import (
    SqlSafetyError,
    collect_schema_context,
    ensure_read_only,
    explain_sql,
    probe_rows,
    render_schema_prompt,
)
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

PROMPT_VERSION = "cluefin-nl2sql-v1"
EMPTY_RESULT_NOTE = "실행해 보니 결과가 0행입니다. 필터를 줄이거나 기간을 넓혀 보세요."
MAX_ATTEMPTS = 3
FOCUS_TABLES = ("real_estate_observations", "real_estate_metrics", "indicator_observations", "indicator_definitions")

SYSTEM_PROMPT = """당신은 Cluefin의 ClickHouse SQL 작성기다. 한국어 질문을 실행 가능한 SELECT 문 하나로 옮긴다.

규칙:
- 아래 <schema>에 있는 테이블과 컬럼만 쓴다. 없는 컬럼을 추측해서 만들지 않는다.
- SELECT 또는 WITH로 시작하는 문장 하나만 만든다. INSERT/ALTER/CREATE/DROP 등 쓰기 문장은 금지다.
- ReplacingMergeTree 테이블은 중복 행이 남을 수 있으므로 `FROM market.<table> FINAL`을 쓴다.
- 필터 값은 <schema>의 `값:`과 `코드→이름:` 목록에서 실제 존재하는 값을 골라 쓴다.
  질문이 한글 이름("미분양", "전세지수")으로 물으면 대응하는 코드(metric_id)로 바꿔 쓴다.
- `값:` 목록은 테이블 전체의 값이고 지표마다 유효한 조합이 다르다. 예를 들어 매매가격지수는
  deal_type이 `매매`이고 미분양은 `전체`다. 질문에 없는 축은 필터하지 말고 metric_id만 걸어라.
  축을 좁힐수록 0행이 될 위험이 커진다.
- 기간을 명시하지 않으면 최근 24개월로 제한하고, 어떤 기간을 골랐는지 notes에 적는다.
- 사람이 표로 읽을 결과를 만든다. 열 이름은 한글 별칭(AS)으로 붙이고, ORDER BY로 정렬하고, LIMIT을 넣는다.
- ClickHouse는 따옴표 없는 한글 식별자를 문법 오류로 본다. 한글 별칭은 반드시 backtick으로 감싼다.
  예: `SELECT toStartOfMonth(period) AS `기간`, round(avg(value), 2) AS `평균` ... GROUP BY `기간``
- 지수(index) 컬럼은 SUM하지 않는다. 평균이나 마지막 값을 쓴다. 호수·물량 같은 개수는 SUM/마지막 값이 맞다.
- 확신이 없는 가정은 notes에 한 줄로 남긴다. SQL 안에 주석으로 설명을 넣지 않는다.

응답은 아래 JSON 하나만 출력한다. 코드블록이나 다른 문장을 덧붙이지 않는다.
{"sql": "SELECT ...", "explanation": "이 쿼리가 무엇을 세는지 한두 문장", "notes": ["가정이나 주의 한 줄", "..."]}"""


class ChatModel(Protocol):
    def invoke(self, messages: list[Any]) -> Any: ...


class SqlAgentState(TypedDict, total=False):
    question: str
    schema_text: str
    sql: str
    explanation: str
    notes: list[str]
    status: str
    error: str
    attempts: int
    empty_retries: int
    model: str


def build_nl_to_sql_graph(
    *,
    client: Any,
    model: ChatModel,
    model_name: str,
    schema_context: dict[str, Any] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
):
    """자연어 → SQL 그래프. `client`는 스키마 조회와 EXPLAIN 검증에만 쓰고 쿼리는 실행하지 않는다."""

    def load_schema(state: SqlAgentState) -> SqlAgentState:
        context = schema_context if schema_context is not None else collect_schema_context(client)
        return {"schema_text": render_schema_prompt(context, focus_tables=FOCUS_TABLES), "attempts": 0}

    def generate(state: SqlAgentState) -> SqlAgentState:
        attempts = int(state.get("attempts", 0)) + 1
        result = model.invoke(
            [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=_user_prompt(state)),
            ]
        )
        payload = _parse_model_json(_message_text(result))
        return {
            "sql": str(payload.get("sql") or "").strip(),
            "explanation": str(payload.get("explanation") or "").strip(),
            "notes": [str(note) for note in (payload.get("notes") or []) if str(note).strip()],
            "attempts": attempts,
            "model": model_name,
            "error": "",
        }

    def validate(state: SqlAgentState) -> SqlAgentState:
        sql = state.get("sql") or ""
        if not sql:
            return {"status": "invalid", "error": "모델이 SQL을 만들지 못했습니다."}
        try:
            prepared = ensure_read_only(sql)
        except SqlSafetyError as exc:
            return {"status": "invalid", "error": str(exc)}
        failure = explain_sql(client, prepared)
        if failure:
            return {"status": "invalid", "error": failure}
        # 문법이 맞아도 조합이 틀리면 0행이 나온다. 한 번은 되먹여 고칠 기회를 주고,
        # 그래도 0행이면 실패로 만들지 않고 사실만 붙여 돌려준다.
        if probe_rows(client, prepared) == 0:
            if int(state.get("empty_retries", 0)) < 1:
                return {
                    "status": "empty",
                    "sql": prepared,
                    "empty_retries": 1,
                    "error": (
                        "쿼리는 유효하지만 결과가 0행이다. 지표에 존재하지 않는 차원 조합을 걸었을 가능성이 높다. "
                        "질문에 없는 축(deal_type·property_type 등)의 필터를 지우고 다시 만들어라."
                    ),
                }
            return {
                "status": "validated",
                "sql": prepared,
                "error": "",
                "notes": [*(state.get("notes") or []), EMPTY_RESULT_NOTE],
            }
        return {"status": "validated", "sql": prepared, "error": ""}

    def route(state: SqlAgentState) -> str:
        if state.get("status") == "validated":
            return "done"
        return "retry" if int(state.get("attempts", 0)) < max_attempts else "done"

    def finalize(state: SqlAgentState) -> SqlAgentState:
        # 0행이 정답인 질문도 있다. 재시도를 다 쓰면 실패로 끝내지 않고 사실만 알려 준다.
        if state.get("status") != "empty":
            return {}
        return {"status": "validated", "notes": [*(state.get("notes") or []), EMPTY_RESULT_NOTE], "error": ""}

    graph = StateGraph(SqlAgentState)
    graph.add_node("load_schema", load_schema)
    graph.add_node("generate_sql", generate)
    graph.add_node("validate_sql", validate)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "load_schema")
    graph.add_edge("load_schema", "generate_sql")
    graph.add_edge("generate_sql", "validate_sql")
    graph.add_conditional_edges("validate_sql", route, {"retry": "generate_sql", "done": "finalize"})
    graph.add_edge("finalize", END)
    return graph.compile()


def _user_prompt(state: SqlAgentState) -> str:
    question = state.get("question", "")
    previous = ""
    if state.get("error"):
        # 실패한 SQL과 서버 오류를 그대로 붙여 같은 실수를 반복하지 않게 한다.
        previous = (
            "\n\n직전 시도가 실패했다. 아래 오류를 고쳐서 다시 만들어라.\n"
            f"<failed_sql>{state.get('sql', '')}</failed_sql>\n"
            f"<error>{state['error']}</error>"
        )
    return f"<schema>\n{state.get('schema_text', '')}\n</schema>\n\n질문: {question}{previous}"


def _message_text(result: Any) -> str:
    content = getattr(result, "content", result)
    if isinstance(content, list):
        return "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
    return str(content)


def _parse_model_json(text: str) -> dict[str, Any]:
    """모델이 코드블록이나 잡담을 덧붙여도 JSON 객체만 건져 낸다."""
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            return {"sql": cleaned}
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return {"sql": cleaned}
    return payload if isinstance(payload, dict) else {"sql": str(payload)}


def translate_question(
    *,
    client: Any,
    model: ChatModel,
    model_name: str,
    question: str,
    schema_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """그래프를 한 번 돌려 웹·CLI가 같은 모양의 결과를 받게 한다."""
    if not (question or "").strip():
        raise ValueError("질문이 비어 있습니다.")
    graph = build_nl_to_sql_graph(
        client=client,
        model=model,
        model_name=model_name,
        schema_context=schema_context,
    )
    result = graph.invoke({"question": question.strip()})
    return {
        "question": question.strip(),
        "sql": result.get("sql", ""),
        "explanation": result.get("explanation", ""),
        "notes": result.get("notes", []),
        "status": result.get("status", "invalid"),
        "error": result.get("error", ""),
        "attempts": result.get("attempts", 0),
        "model": result.get("model", model_name),
        "prompt_version": PROMPT_VERSION,
    }
