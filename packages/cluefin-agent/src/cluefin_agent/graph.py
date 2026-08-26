from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Protocol, TypedDict
from uuid import uuid4

from cluefin_store.models import DailyResearchReport
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from cluefin_agent.repository import ResearchDataRepository

PROMPT_VERSION = "cluefin-daily-v1"


class ChatModel(Protocol):
    def invoke(self, messages: list[Any]) -> Any: ...


class ReportState(TypedDict, total=False):
    report_date: str
    snapshot: dict[str, Any]
    prompt: str
    markdown: str
    title: str
    model: str
    status: str
    persisted: bool


SYSTEM_PROMPT = """당신은 Cluefin의 한국어 시장 리서치 에이전트다.
제공된 ClickHouse 스냅샷만 근거로 사용한다. 값이 없거나 유료 연결이 필요한 지표는 없다고 명시한다.
상관관계를 인과관계처럼 단정하지 않고, 최근 방향과 절대 수준, 반대 근거를 함께 쓴다.
패턴과 시그널은 매수 추천이 아니라 과거 검증 근거 및 관찰 후보로 표현한다.
모든 숫자는 입력에 있는 값만 사용하고 투자 권유 문구를 쓰지 않는다.
응답은 간결하지만 초보자가 이유를 이해할 수 있는 한국어 Markdown이어야 한다."""


def prepare_report_prompt(snapshot: dict[str, Any]) -> str:
    evidence = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"""다음은 {snapshot["report_date"]} 기준 Cluefin 데이터 스냅샷이다.

아래 순서로 일일 분석 리포트를 작성하라.
# 오늘의 시장 리포트
## 한눈에 보는 결론
## 글로벌 거시와 유동성
## 한국 주식과 기업 펀더멘털
## 코인·온체인·파생 포지셔닝
## 패턴 검증과 오늘의 시그널
## 오늘 확인할 체크리스트
## 데이터 한계와 반대 근거

각 분석 문단은 '관측 → 일반적 영향 → 이번 해석 → 틀릴 수 있는 조건' 순서로 쓴다.
마지막에 관찰 우선순위 3개를 제시하되 매수·매도 명령은 하지 않는다.

<snapshot>{evidence}</snapshot>"""


def build_daily_report_graph(
    *,
    repository: ResearchDataRepository,
    model: ChatModel,
    model_name: str,
    persist: bool = True,
):
    def collect(state: ReportState) -> ReportState:
        report_date = date.fromisoformat(state["report_date"])
        return {"snapshot": repository.collect_snapshot(report_date)}

    def prepare(state: ReportState) -> ReportState:
        return {"prompt": prepare_report_prompt(state["snapshot"])}

    def generate(state: ReportState) -> ReportState:
        result = model.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=state["prompt"])])
        content = result.content
        if isinstance(content, list):
            content = "\n".join(
                str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content
            )
        markdown = str(content).strip()
        return {
            "markdown": markdown,
            "title": _report_title(markdown, state["report_date"]),
            "model": model_name,
            "status": "generated",
        }

    def validate(state: ReportState) -> ReportState:
        required = ("한눈에", "글로벌", "한국", "코인", "패턴", "데이터 한계")
        missing = [heading for heading in required if heading not in state["markdown"]]
        if missing:
            suffix = (
                "\n\n## 데이터 한계와 반대 근거\n- 자동 생성 초안에서 일부 필수 섹션이 누락되었습니다: "
                + ", ".join(missing)
            )
            return {"markdown": state["markdown"] + suffix, "status": "partial"}
        return {"status": "validated"}

    def save(state: ReportState) -> ReportState:
        if not persist:
            return {"persisted": False}
        snapshot = state["snapshot"]
        report = DailyResearchReport(
            report_date=date.fromisoformat(state["report_date"]),
            report_id=uuid4(),
            model=state["model"],
            status=state["status"],
            title=state["title"],
            markdown=state["markdown"],
            summary_json=json.dumps(snapshot.get("coverage", {}), ensure_ascii=False, sort_keys=True),
            source_snapshot_json=json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
            prompt_version=PROMPT_VERSION,
            generated_at=datetime.now(),
        )
        repository.save_report(report)
        return {"persisted": True}

    graph = StateGraph(ReportState)
    graph.add_node("collect_evidence", collect)
    graph.add_node("prepare_prompt", prepare)
    graph.add_node("generate_report", generate)
    graph.add_node("validate_report", validate)
    graph.add_node("persist_report", save)
    graph.add_edge(START, "collect_evidence")
    graph.add_edge("collect_evidence", "prepare_prompt")
    graph.add_edge("prepare_prompt", "generate_report")
    graph.add_edge("generate_report", "validate_report")
    graph.add_edge("validate_report", "persist_report")
    graph.add_edge("persist_report", END)
    return graph.compile()


def _report_title(markdown: str, report_date: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return f"{report_date} 시장 리포트"
