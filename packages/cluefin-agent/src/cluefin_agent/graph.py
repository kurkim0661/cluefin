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
패턴과 시그널은 과거 검증 근거이자 관찰 후보이며, 그 자체로 매매 신호가 아니다.
모든 숫자는 입력에 있는 값만 사용한다.

이전 리포트 활용 규칙:
- 스냅샷의 `previous_reports`는 최근 리포트가 남긴 결론·포지션·체크리스트다. 이번 리포트는 그 연장선에서 쓴다.
- 이전 리포트가 "지켜봐야 한다"거나 "이렇게 흘러갈 것"이라고 적은 항목을 먼저 찾고, 지금 데이터로 그 예상이 맞았는지 빗나갔는지 판정한다.
- 판정은 `이어짐 / 반전 / 미확인` 중 하나로 명시하고, 그렇게 본 근거 지표를 함께 적는다.
- `previous_reports`가 비어 있으면 "비교할 이전 리포트가 없다"고 한 줄로 적고 넘어간다. 없는 리포트를 지어내지 않는다.

포지션 스탠스 규칙:
- 각 자산군에 대해 `비중 확대 / 유지 / 축소 / 관망` 중 하나를 반드시 고른다. "상황에 따라 다르다"로 회피하지 않는다.
- 스탠스는 확정적 매매 지시가 아니라 조건과 무효화 신호를 붙인 시나리오로 쓴다. 개별 종목 매수·매도 지시는 하지 않고 자산군 단위로만 말한다.
- 근거가 약하거나 데이터가 없으면 그 사실을 근거로 `관망`을 고른다. 데이터 없음을 스탠스 누락의 이유로 쓰지 않는다.
- 목표가·손절가·기대수익률 같은 숫자는 스냅샷에 없으므로 쓰지 않는다.

부동산 규칙:
- 스냅샷의 `real_estate`는 지표(metric)×지역×주택유형 조합이다. 나열하지 말고 수도권 판단에 필요한 축만 골라 쓴다.
- 가격지수는 `change_pct_display`(직전 대비)와 `yoy_pct_display`(전년 대비)를 함께 봐야 한다. 한 달 반등과 추세 전환을 구분한다.
- 매매·전세·월세 지수 방향이 엇갈리면 그 괴리를 그대로 쓴다. 미분양·인허가는 공급 측 선행 신호로 다룬다.

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
- 마크다운 표는 최대 5열까지만 쓰고, 표 안에서는 문장 대신 짧은 구를 쓴다.
응답은 간결하지만 초보자가 이유를 이해할 수 있는 한국어 Markdown이어야 한다."""


def prepare_report_prompt(snapshot: dict[str, Any]) -> str:
    evidence = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"""다음은 {snapshot["report_date"]} 기준 Cluefin 데이터 스냅샷이다.

아래 순서로 일일 분석 리포트를 작성하라.
# 오늘의 시장 리포트
## 한눈에 보는 결론
## 지난 리포트 점검
## 글로벌 거시와 유동성
## 한국 주식과 기업 펀더멘털
## 코인·온체인·파생 포지셔닝
## 부동산과 주거 시장
## 패턴 검증과 오늘의 시그널
## 자산별 포지션 가이드
## 오늘 확인할 체크리스트
## 데이터 한계와 반대 근거

`## 한눈에 보는 결론`은 아래 순서로 쓴다.
1. 오늘 시장을 한 문장으로 요약한다.
2. `| 영역 | 핵심 지표 | 값 | 최근 변화 | 판단 |` 5열 표로 거시·한국·기업실적·코인·부동산 중 중요한 4~6줄만 담는다.
3. 표 아래에 '지금 가장 중요한 것' 불릿 3개를 쓴다.

`## 지난 리포트 점검`은 `previous_reports`를 근거로 아래 순서로 쓴다.
1. 이전 리포트가 예상하거나 지켜보자고 한 항목 2~3개를 고른다.
2. `| 지난 리포트 | 그때의 관점 | 지금 데이터 | 판정 |` 4열 표로 정리하고, 판정은 `이어짐 / 반전 / 미확인` 중 하나로 쓴다.
3. 표 아래에 그 흐름이 지금 어디쯤 와 있는지 두세 문장으로 잇는다.

`## 부동산과 주거 시장`은 `real_estate`만 근거로 쓴다. 매매·전세 가격지수 방향, 수도권과 전국의 격차,
공급(미분양·인허가) 신호 중 중요한 관찰 블록 2~3개를 고른다.

`## 자산별 포지션 가이드`는 아래 순서로 쓴다.
1. `| 자산 | 스탠스 | 근거 | 무효화 신호 |` 4열 표를 쓴다. 스탠스는 `비중 확대 / 유지 / 축소 / 관망` 중 하나다.
2. 자산 행은 `한국 주식`, `미국 주식`, `금리·채권`, `코인`, `부동산`을 모두 채운다.
3. 표 아래에 '지금 가장 바꿀 것' 한 문장과, 이 스탠스가 투자 권유가 아니라 데이터 기반 시나리오라는 한 문장을 쓴다.

나머지 섹션은 소제목(`#### 블록 제목`)과 `관측 / 영향 / 해석 / 반대 근거` 불릿 네 줄 구조의 관찰 블록으로 채운다.
마지막에 관찰 우선순위 3개를 제시하되 개별 종목 매수·매도 명령은 하지 않는다.

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
        required = ("한눈에", "지난 리포트", "글로벌", "한국", "코인", "부동산", "패턴", "포지션", "데이터 한계")
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
