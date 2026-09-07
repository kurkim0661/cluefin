"""실제 ClickHouse에 대고 모든 읽기 경로를 실행한다.

가짜 클라이언트는 SQL을 파싱하지 않으므로 타입 오류를 잡지 못한다. 실제로 리포트 생성을 깨뜨린
버그(code 386: `toString(period) AS period` 별칭이 컬럼을 가려 String과 Date를 비교)가 그 예다.
게다가 `_query_rows`는 예외를 삼켜 빈 목록을 돌려주므로, 대시보드에서는 오류가 아니라 '빈 패널'로
보인다. 여기서는 예외 삼킴을 끄고 각 쿼리를 실제로 실행해 조용한 실패를 드러낸다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def repository():
    clickhouse_connect = pytest.importorskip("clickhouse_connect")
    from cluefin_web.repository import DashboardRepository

    try:
        repo = DashboardRepository.from_env()
        repo.client.query("SELECT 1")
    except Exception as exc:  # ClickHouse가 없는 환경에서는 조용히 건너뛴다.
        pytest.skip(f"ClickHouse에 연결할 수 없습니다: {type(exc).__name__}")

    def loud_query_rows(self, sql, *, fallback_columns, parameters=None):
        with self._query_lock:
            result = self.client.query(sql, parameters=parameters) if parameters else self.client.query(sql)
        columns = getattr(result, "column_names", None) or fallback_columns
        return [dict(zip(columns, row, strict=False)) for row in result.result_rows]

    DashboardRepository._query_rows = loud_query_rows
    assert clickhouse_connect is not None
    return repo


@pytest.mark.parametrize(
    "name",
    [
        "snapshot",
        "market_pulse",
        "latest_research_report",
        "real_estate_meta",
        "pattern_performance",
        "latest_signals",
        "universe_members",
        "latest_technicals",
        "latest_sentiment",
        "paper_dashboard",
        "saved_sql_queries",
    ],
)
def test_read_paths_execute_without_type_errors(repository, name: str) -> None:
    getattr(repository, name)()


def test_report_history_and_chart_paths_execute(repository) -> None:
    repository.research_report_history(5)
    repository.symbol_chart("005930")
    repository.sql_schema(refresh=True)


def test_backtest_inputs_execute(repository) -> None:
    end = date.today()
    repository._backtest_signals(end - timedelta(days=365), end)
    repository._latest_market_date()
