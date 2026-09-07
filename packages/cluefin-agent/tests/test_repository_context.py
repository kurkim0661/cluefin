from __future__ import annotations

from datetime import date

from cluefin_agent.repository import ResearchDataRepository, extract_forward_sections


class FakeResult:
    def __init__(self, columns: list[str], rows: list[tuple]) -> None:
        self.column_names = columns
        self.result_rows = rows


class FakeClient:
    """Answers by matching the table name in the SQL, so query text can change freely."""

    def __init__(self, tables: dict[str, FakeResult]) -> None:
        self.tables = tables
        self.queries: list[str] = []

    def query(self, sql: str) -> FakeResult:
        self.queries.append(sql)
        for table, result in self.tables.items():
            if table in sql:
                return result
        return FakeResult([], [])


class FakeStore:
    def __init__(self, tables: dict[str, FakeResult]) -> None:
        self.fake_client = FakeClient(tables)

    def client(self) -> FakeClient:
        return self.fake_client


def _real_estate_repository() -> ResearchDataRepository:
    # 14개월치를 넣어야 직전 대비와 전년 대비가 모두 계산된다.
    rows = [
        (
            "house_sale_price_index",
            "서울",
            "수도권",
            "아파트",
            "매매",
            "2026.01=100",
            f"{'2025' if index <= 12 else '2026'}-{((index - 1) % 12) + 1:02d}-01",
            100.0 + index,
        )
        for index in range(1, 15)
    ]
    return ResearchDataRepository(
        FakeStore(
            {
                "market.real_estate_metrics": FakeResult(
                    [
                        "metric_id",
                        "name_ko",
                        "category",
                        "deal_type",
                        "unit",
                        "frequency",
                        "higher_is",
                        "interpretation_ko",
                    ],
                    [
                        (
                            "house_sale_price_index",
                            "주택 매매가격지수",
                            "price",
                            "매매",
                            "2026.01=100",
                            "monthly",
                            "up",
                            "지수가 오르면 매매가가 오른 것이다.",
                        )
                    ],
                ),
                "market.real_estate_observations": FakeResult(
                    [
                        "metric_id",
                        "region",
                        "region_tier",
                        "property_type",
                        "deal_type",
                        "unit",
                        "period",
                        "value",
                    ],
                    rows,
                ),
            }
        )
    )


def test_collect_real_estate_reports_latest_change_and_year_over_year() -> None:
    repository = _real_estate_repository()

    series = repository.collect_real_estate(date(2026, 2, 28))

    assert len(series) == 1
    entry = series[0]
    assert entry["name_ko"] == "주택 매매가격지수"
    assert entry["region"] == "서울"
    assert entry["property_type"] == "아파트"
    assert entry["value_display"] == "114 2026.01=100"
    assert entry["change_display"] == "+1 2026.01=100"
    # 14번째 관측의 전년 동월은 두 번째 관측(102.0)이다.
    assert entry["yoy_pct_display"] == "+11.76%"
    assert entry["points"] == 14


def test_collect_real_estate_filters_to_the_capital_area_axes() -> None:
    repository = _real_estate_repository()

    repository.collect_real_estate(date(2026, 2, 28))

    observation_sql = next(sql for sql in repository.client.queries if "real_estate_observations" in sql)
    assert "'전국', '수도권', '서울', '경기', '인천'" in observation_sql
    assert "'종합', '아파트', '전체'" in observation_sql


def test_previous_reports_keep_the_newest_run_per_day_with_forward_sections() -> None:
    markdown = (
        "# 오늘의 시장 리포트\n"
        "## 한눈에 보는 결론\n혼조다.\n"
        "## 글로벌 거시와 유동성\n실질금리가 내렸다.\n"
        "## 오늘 확인할 체크리스트\n- 전세가율 반등 여부\n"
    )
    repository = ResearchDataRepository(
        FakeStore(
            {
                "market.daily_research_reports": FakeResult(
                    # 별칭이 컬럼을 가리지 않도록 report_day로 받는다.
                    ["report_day", "title", "status", "markdown"],
                    [
                        ("2026-08-30", "재실행본", "validated", markdown),
                        ("2026-08-30", "첫 실행본", "partial", markdown),
                        ("2026-08-29", "그제 리포트", "validated", markdown),
                    ],
                )
            }
        )
    )

    reports = repository.previous_reports(date(2026, 8, 31), limit=2)

    assert [item["report_date"] for item in reports] == ["2026-08-30", "2026-08-29"]
    assert reports[0]["title"] == "재실행본"
    assert reports[0]["sections"] == {
        "한눈에 보는 결론": "혼조다.",
        "오늘 확인할 체크리스트": "- 전세가율 반등 여부",
    }


def test_extract_forward_sections_drops_narrative_sections_and_truncates() -> None:
    markdown = "## 글로벌 거시와 유동성\n" + ("가" * 50) + "\n## 자산별 포지션 가이드\n" + ("나" * 2000)

    sections = extract_forward_sections(markdown)

    assert "글로벌 거시와 유동성" not in sections
    assert len(sections["자산별 포지션 가이드"]) == 900


def test_extract_forward_sections_returns_nothing_for_an_empty_report() -> None:
    assert extract_forward_sections("") == {}


def test_date_columns_are_not_shadowed_by_their_own_string_alias() -> None:
    """`toString(period) AS period` 뒤에 `WHERE period >= toDate(...)`를 쓰면 ClickHouse가 거부한다.

    별칭이 컬럼을 가려 String과 Date를 비교하게 되고(code 386 NO_COMMON_TYPE) 리포트 생성이 통째로
    실패한다. 실제 DB 없이도 재발을 잡기 위해 SQL 문자열을 훑는다.
    """
    import re
    from pathlib import Path

    import cluefin_agent.repository as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    offenders: list[str] = []
    for statement in re.findall(r'"""\s*(SELECT.*?)"""', source, flags=re.DOTALL | re.IGNORECASE):
        shadowed = set(re.findall(r"toString\(\s*(?:\w+\.)?(\w+)\s*\)\s+AS\s+\1\b", statement, flags=re.IGNORECASE))
        for name in shadowed:
            # 날짜 리터럴과의 비교만 문제가 된다. ORDER BY는 문자열 정렬이라 결과가 같다.
            compared = re.search(
                rf"\b{name}\b\s*(?:>=|<=|<|>|=|BETWEEN)\s*to(?:Date|DateTime)", statement, flags=re.IGNORECASE
            )
            if compared:
                offenders.append(f"{name}: {statement.strip()[:80]}")

    assert offenders == [], "날짜 컬럼을 같은 이름의 문자열 별칭으로 가린 뒤 비교하고 있습니다: " + "; ".join(offenders)
