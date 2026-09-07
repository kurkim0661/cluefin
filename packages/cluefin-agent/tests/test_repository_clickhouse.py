"""리포트 근거 수집을 실제 ClickHouse로 실행한다.

`collect_snapshot`은 LLM을 부르기 전 단계이고, 여기서 실패하면 리포트 생성이 통째로 죽는다.
실제로 `toString(period) AS period` 별칭이 컬럼을 가려 code 386으로 죽은 적이 있는데,
가짜 클라이언트 테스트로는 잡히지 않았다.
"""

from __future__ import annotations

from datetime import date

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def repository():
    pytest.importorskip("clickhouse_connect")
    from cluefin_store.db import ClickHouseStore

    from cluefin_agent.repository import ResearchDataRepository

    store = ClickHouseStore()
    try:
        store.client().query("SELECT 1")
    except Exception as exc:
        pytest.skip(f"ClickHouse에 연결할 수 없습니다: {type(exc).__name__}")
    return ResearchDataRepository(store)


def test_collect_snapshot_runs_every_evidence_query(repository) -> None:
    snapshot = repository.collect_snapshot(date.today())

    for key in ("indicators", "patterns", "signals", "universe", "real_estate", "previous_reports", "coverage"):
        assert key in snapshot


def test_real_estate_evidence_filters_by_date_without_type_errors(repository) -> None:
    series = repository.collect_real_estate(date.today())

    # 조회가 성공하면 각 항목은 표시용 문자열까지 채워져 있어야 한다.
    for item in series[:5]:
        assert item["metric_id"]
        assert "value_display" in item
        assert "period" in item


def test_previous_reports_query_compares_dates_on_the_real_column(repository) -> None:
    reports = repository.previous_reports(date.today(), limit=2)

    assert len(reports) <= 2
    for report in reports:
        assert report["report_date"]
