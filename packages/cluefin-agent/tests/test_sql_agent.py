from __future__ import annotations

import json

import pytest

from cluefin_agent.sql_agent import build_nl_to_sql_graph, translate_question


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeModel:
    """미리 정해둔 응답을 순서대로 돌려주고 받은 프롬프트를 기록한다."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def invoke(self, messages: list) -> FakeMessage:
        self.prompts.append(str(messages[-1].content))
        index = min(len(self.prompts) - 1, len(self.responses) - 1)
        return FakeMessage(self.responses[index])


class FakeResult:
    def __init__(self, rows: list[tuple], columns: list[str]) -> None:
        self.result_rows = rows
        self.column_names = columns


class FakeClient:
    """EXPLAIN 결과와 실행 행 수를 따로 흉내 낸다. 둘 다 에이전트의 판단 근거다."""

    def __init__(
        self,
        explain_errors: list[str | None] | None = None,
        probe_counts: list[int] | None = None,
    ) -> None:
        self.explain_errors = explain_errors or [None]
        self.probe_counts = probe_counts or [1]
        self.explains = 0
        self.probes = 0

    def query(self, sql: str, parameters: dict | None = None, settings: dict | None = None) -> FakeResult:
        if sql.lstrip().upper().startswith("EXPLAIN"):
            error = self.explain_errors[min(self.explains, len(self.explain_errors) - 1)]
            self.explains += 1
            if error:
                raise RuntimeError(f"Code: 47. DB::Exception: {error}")
            return FakeResult([("Expression",)], ["explain"])
        count = self.probe_counts[min(self.probes, len(self.probe_counts) - 1)]
        self.probes += 1
        return FakeResult([("2026-06-01", 1.0)] * count, ["기간", "값"])


SCHEMA_CONTEXT = {
    "database": "market",
    "tables": [
        {
            "database": "market",
            "table": "real_estate_observations",
            "engine": "ReplacingMergeTree",
            "sorting_key": "metric_id, period",
            "total_rows": 15216,
            "columns": [
                {"name": "period", "type": "Date", "role": "time", "comment": ""},
                {"name": "metric_id", "type": "LowCardinality(String)", "role": "dimension", "comment": ""},
                {"name": "value", "type": "Float64", "role": "fact", "comment": ""},
            ],
            "samples": {"metric_id": ["unsold_housing"]},
            "period": {"column": "period", "first": "2016-09-01", "last": "2026-06-01"},
            "catalog": [],
        }
    ],
}

GOOD_SQL = "SELECT period AS 기간, avg(value) AS 평균 FROM market.real_estate_observations FINAL GROUP BY period"
GOOD_RESPONSE = json.dumps(
    {"sql": GOOD_SQL, "explanation": "월별 평균을 낸다.", "notes": ["기간 미지정이라 최근 24개월"]},
    ensure_ascii=False,
)


def _translate(model: FakeModel, client: FakeClient, question: str = "미분양 추이 보여줘") -> dict:
    return translate_question(
        client=client,
        model=model,
        model_name="test-model",
        question=question,
        schema_context=SCHEMA_CONTEXT,
    )


def test_translate_question_returns_validated_sql_with_limit_appended() -> None:
    model = FakeModel([GOOD_RESPONSE])

    result = _translate(model, FakeClient())

    assert result["status"] == "validated"
    assert result["sql"].startswith("SELECT period AS 기간")
    assert result["sql"].rstrip().endswith("LIMIT 2000")
    assert result["explanation"] == "월별 평균을 낸다."
    assert result["notes"] == ["기간 미지정이라 최근 24개월"]
    assert result["attempts"] == 1
    assert result["model"] == "test-model"


def test_prompt_carries_schema_values_and_the_question() -> None:
    model = FakeModel([GOOD_RESPONSE])

    _translate(model, FakeClient(), question="수도권 미분양과 매매지수 비교")

    prompt = model.prompts[0]
    assert "market.real_estate_observations" in prompt
    assert "값: unsold_housing" in prompt
    assert "수도권 미분양과 매매지수 비교" in prompt


def test_model_output_wrapped_in_code_fence_is_parsed() -> None:
    model = FakeModel([f"여기 있습니다.\n```json\n{GOOD_RESPONSE}\n```\n"])

    result = _translate(model, FakeClient())

    assert result["status"] == "validated"
    assert "FROM market.real_estate_observations FINAL" in result["sql"]


def test_explain_failure_is_fed_back_and_retried() -> None:
    bad = json.dumps({"sql": "SELECT nope FROM market.real_estate_observations", "explanation": "", "notes": []})
    model = FakeModel([bad, GOOD_RESPONSE])
    client = FakeClient(explain_errors=["Unknown expression identifier `nope`.", None])

    result = _translate(model, client)

    assert result["status"] == "validated"
    assert result["attempts"] == 2
    assert "Unknown expression identifier `nope`." in model.prompts[1]
    assert "<failed_sql>SELECT nope FROM market.real_estate_observations</failed_sql>" in model.prompts[1]


def test_write_statement_is_rejected_without_calling_clickhouse() -> None:
    model = FakeModel([json.dumps({"sql": "DROP TABLE market.real_estate_observations"})])
    client = FakeClient()

    result = _translate(model, client)

    assert result["status"] == "invalid"
    assert "SELECT" in result["error"]
    assert client.explains == 0


def test_agent_gives_up_after_max_attempts_and_reports_the_error() -> None:
    bad = json.dumps({"sql": "SELECT nope FROM market.real_estate_observations"})
    model = FakeModel([bad])
    client = FakeClient(explain_errors=["Unknown expression identifier `nope`."])

    graph = build_nl_to_sql_graph(
        client=client,
        model=model,
        model_name="test-model",
        schema_context=SCHEMA_CONTEXT,
        max_attempts=2,
    )
    result = graph.invoke({"question": "이상한 질문"})

    assert result["status"] == "invalid"
    assert result["attempts"] == 2
    assert "nope" in result["error"]


def test_empty_question_is_refused() -> None:
    with pytest.raises(ValueError):
        _translate(FakeModel([GOOD_RESPONSE]), FakeClient(), question="   ")


def test_system_prompt_pins_clickhouse_gotchas() -> None:
    from cluefin_agent.sql_agent import SYSTEM_PROMPT

    # ClickHouse는 따옴표 없는 한글 별칭을 문법 오류로 본다. 이 규칙이 빠지면 매번 EXPLAIN에서 튕긴다.
    assert "backtick" in SYSTEM_PROMPT
    assert "FINAL" in SYSTEM_PROMPT
    assert "SUM하지 않는다" in SYSTEM_PROMPT


def test_zero_row_result_is_fed_back_once_so_the_model_can_widen_filters() -> None:
    """EXPLAIN은 통과하지만 0행인 SQL은 흔한 실패다. 한 번은 고칠 기회를 준다."""
    narrow = json.dumps(
        {"sql": "SELECT 1 AS a FROM market.real_estate_observations WHERE deal_type = '전체'", "notes": []}
    )
    model = FakeModel([narrow, GOOD_RESPONSE])
    client = FakeClient(probe_counts=[0, 1])

    result = _translate(model, client)

    assert result["status"] == "validated"
    assert result["attempts"] == 2
    assert "0행" in model.prompts[1]
    assert "deal_type" in model.prompts[1]


def test_zero_row_result_survives_as_a_note_instead_of_a_failure() -> None:
    """0행이 정답인 질문도 있다. 재시도를 다 쓰면 실패가 아니라 사실로 알린다."""
    model = FakeModel([GOOD_RESPONSE])
    client = FakeClient(probe_counts=[0])

    result = _translate(model, client)

    assert result["status"] == "validated"
    assert result["error"] == ""
    assert any("0행" in note for note in result["notes"])
    # 빈 결과 되먹임은 한 번만 한다. 무한 재시도로 토큰을 태우지 않는다.
    assert result["attempts"] == 2


def test_prompt_warns_that_dimension_combinations_differ_per_metric() -> None:
    from cluefin_agent.sql_agent import SYSTEM_PROMPT

    assert "지표마다 유효한 조합이 다르다" in SYSTEM_PROMPT
