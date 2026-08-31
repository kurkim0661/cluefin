from __future__ import annotations

from langchain_core.messages import AIMessage

from cluefin_agent.graph import build_daily_report_graph, prepare_report_prompt


class FakeRepository:
    def __init__(self) -> None:
        self.saved = []

    def collect_snapshot(self, report_date):
        return {
            "report_date": report_date.isoformat(),
            "indicators": [{"name_ko": "미국 실질금리", "value": 2.3, "change": -0.1}],
            "patterns": [{"pattern_name": "double_bottom", "sample_count": 50}],
            "signals": [{"symbol": "005930", "pattern_name": "double_bottom"}],
            "universe": [{"market_country": "KR", "symbols": 50}],
            "coverage": {"observed": 1, "total": 1, "unavailable": []},
        }

    def save_report(self, report):
        self.saved.append(report)
        return 1


class FakeModel:
    def invoke(self, messages):
        assert "<snapshot>" in messages[1].content
        return AIMessage(
            content="# 오늘의 시장 리포트\n## 한눈에\n혼조\n## 글로벌\n금리\n## 한국\n주식\n## 코인\n온체인\n## 패턴\n관찰\n## 데이터 한계\n표본"
        )


def test_prepare_report_prompt_contains_evidence_contract() -> None:
    prompt = prepare_report_prompt({"report_date": "2026-08-26", "coverage": {"observed": 10}})

    assert "관측 / 영향 / 해석 / 반대 근거" in prompt
    assert "| 영역 | 핵심 지표 | 값 | 최근 변화 | 판단 |" in prompt
    assert '"observed":10' in prompt


def test_system_prompt_requires_line_broken_blocks_and_display_numbers() -> None:
    from cluefin_agent.graph import SYSTEM_PROMPT

    assert "value_display" in SYSTEM_PROMPT
    assert "78601.4032035769" in SYSTEM_PROMPT
    assert "네 단계를 한 문단에 이어 붙이지 않는다" in SYSTEM_PROMPT


def test_langgraph_generates_validates_and_persists_report() -> None:
    repository = FakeRepository()
    graph = build_daily_report_graph(repository=repository, model=FakeModel(), model_name="test-model")

    result = graph.invoke({"report_date": "2026-08-26"})

    assert result["status"] == "validated"
    assert result["persisted"] is True
    assert result["model"] == "test-model"
    assert repository.saved[0].report_date.isoformat() == "2026-08-26"
    assert "미국 실질금리" in repository.saved[0].source_snapshot_json
