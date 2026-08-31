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

숫자 표기 규칙:
- 스냅샷의 `value_display`, `change_display`, `change_pct_display` 문자열을 그대로 옮겨 쓴다.
- 원시 `value`의 긴 소수점을 그대로 쓰지 않는다. `78601.4032035769`가 아니라 `78,601 USD`처럼 쓴다.
- 표시 문자열이 없을 때만 직접 반올림하되 소수점 둘째 자리까지만 쓴다.

가독성 규칙:
- 네 단계를 한 문단에 이어 붙이지 않는다. 아래처럼 각 단계를 별도 줄의 불릿으로 쓴다.

  #### 실질금리 하락
  - **관측**: 미국 10년 실질금리 `2.32%`, 5거래일 `-0.06%p`.
  - **영향**: 실질금리 하락은 성장주와 코인의 할인율 부담을 줄인다.
  - **해석**: 방향은 우호적이지만 절대 수준은 여전히 과거 상단에 가깝다.
  - **반대 근거**: 기대인플레이션이 다시 오르면 이 완화 신호는 사라진다.

- 한 관찰 블록은 소제목 한 줄과 불릿 네 줄로 끝낸다. 각 불릿은 한두 문장, 80자 이내로 끊는다.
- 한 섹션에는 관찰 블록을 2~3개만 넣는다. 지표를 나열하지 말고 중요한 것부터 고른다.
- 섹션 첫머리에는 그 섹션의 결론을 한 문장으로 먼저 쓴다.
- 마크다운 표는 `| 지표 | 값 | 최근 변화 | 판단 |` 형태의 4열까지만 쓴다. 표 안에서는 문장을 쓰지 않는다.
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

`## 한눈에 보는 결론`은 아래 순서로 쓴다.
1. 오늘 시장을 한 문장으로 요약한다.
2. `| 영역 | 핵심 지표 | 값 | 최근 변화 | 판단 |` 5열 표로 거시·한국·기업실적·코인 중 중요한 4~6줄만 담는다.
3. 표 아래에 '지금 가장 중요한 것' 불릿 3개를 쓴다.

나머지 섹션은 소제목(`#### 블록 제목`)과 `관측 / 영향 / 해석 / 반대 근거` 불릿 네 줄 구조의 관찰 블록으로 채운다.
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
