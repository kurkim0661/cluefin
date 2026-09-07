from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from cluefin_store.db import ClickHouseStore
from cluefin_store.models import DailyResearchReport

# 부동산 팩트 테이블은 권역·주택유형 조합이 많아 전부 넣으면 프롬프트가 지표 나열로 변한다.
# 수도권 판단에 필요한 축만 남긴다.
REAL_ESTATE_REGIONS = ("전국", "수도권", "서울", "경기", "인천")
REAL_ESTATE_PROPERTY_TYPES = ("종합", "아파트", "전체")
REAL_ESTATE_LOOKBACK_DAYS = 800

# 이전 리포트는 전문을 넣지 않고 "다음에 확인하겠다"고 적어둔 부분만 회수한다.
PREVIOUS_REPORT_LIMIT = 3
PREVIOUS_REPORT_SECTION_KEYWORDS = ("한눈에", "포지션", "체크리스트", "점검", "우선순위", "반대 근거")
PREVIOUS_REPORT_SECTION_CHARS = 900


class ResearchDataRepository:
    def __init__(self, store: ClickHouseStore) -> None:
        self.store = store
        self.client = store.client()

    def collect_snapshot(self, report_date: date) -> dict[str, Any]:
        definitions = self._rows(
            """
            SELECT indicator_id, name_ko, domain, category, unit, frequency,
                   higher_is, importance, interpretation_ko, availability
            FROM market.indicator_definitions FINAL
            ORDER BY importance DESC, indicator_id
            """
        )
        start = report_date - timedelta(days=400)
        observations = self._rows(
            f"""
            SELECT indicator_id, toString(period) AS observed_on, value, provider
            FROM market.indicator_observations FINAL
            WHERE period BETWEEN toDate('{start.isoformat()}') AND toDate('{report_date.isoformat()}')
            ORDER BY indicator_id, period
            """
        )
        series: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in observations:
            series[str(row["indicator_id"])].append(row)
        indicators: list[dict[str, Any]] = []
        for definition in definitions:
            history = series.get(str(definition["indicator_id"]), [])
            latest = history[-1] if history else None
            previous = history[-2] if len(history) > 1 else None
            change = float(latest["value"]) - float(previous["value"]) if latest and previous else None
            change_pct = change / abs(float(previous["value"])) if change is not None and previous["value"] else None
            unit = definition.get("unit")
            value = float(latest["value"]) if latest else None
            # The model quotes these strings verbatim, so raw float precision never reaches the report.
            indicators.append(
                {
                    **definition,
                    "value": value,
                    "value_display": display_number(value, unit),
                    "period": latest.get("observed_on") if latest else None,
                    "provider": latest.get("provider") if latest else None,
                    "change": change,
                    "change_display": display_number(change, unit, signed=True),
                    "change_pct": change_pct,
                    "change_pct_display": display_percent(change_pct),
                    "points": len(history),
                }
            )

        patterns = self._rows(
            """
            SELECT pattern_name, horizon_days, htf_trend, retest_confirmed,
                   sample_count, target_hit_rate, avg_return_at_horizon,
                   avg_confluence_score
            FROM market.pattern_performance_summary
            ORDER BY sample_count DESC, avg_return_at_horizon DESC
            LIMIT 40
            """
        )
        signals = self._rows(
            f"""
            SELECT symbol, pattern_name, toString(detection_date) AS detected_on,
                   direction, confluence_score, htf_trend, retest_confirmed
            FROM market.daily_pattern_events FINAL
            WHERE detection_date >= toDate('{report_date.isoformat()}') - 30
            ORDER BY confluence_score DESC, detection_date DESC
            LIMIT 40
            """
        )
        universe = self._rows(
            """
            SELECT market_country, countDistinct(symbol) AS symbols,
                   countIf(ema_20 > ema_50) AS bullish_trend,
                   countIf(rsi_14 >= 70) AS overbought,
                   countIf(rsi_14 <= 35) AS oversold
            FROM
            (
                SELECT u.market_country, u.symbol,
                       argMax(t.ema_20, t.trade_date) AS ema_20,
                       argMax(t.ema_50, t.trade_date) AS ema_50,
                       argMax(t.rsi_14, t.trade_date) AS rsi_14
                FROM market.daily_universe_members AS u FINAL
                LEFT JOIN market.daily_technical_features AS t FINAL
                    ON u.provider = t.provider AND u.symbol = t.symbol
                WHERE (u.provider, u.universe_name, u.trade_date) IN
                (
                    SELECT provider, universe_name, max(trade_date)
                    FROM market.daily_universe_members
                    GROUP BY provider, universe_name
                )
                GROUP BY u.market_country, u.symbol
            )
            GROUP BY market_country
            ORDER BY market_country
            """
        )
        real_estate = self.collect_real_estate(report_date)
        previous_reports = self.previous_reports(report_date)
        unavailable = [
            {"indicator_id": item["indicator_id"], "name_ko": item["name_ko"], "availability": item["availability"]}
            for item in indicators
            if item["value"] is None
        ]
        return {
            "report_date": report_date.isoformat(),
            "indicators": indicators,
            "patterns": patterns,
            "signals": signals,
            "universe": universe,
            "real_estate": real_estate,
            "previous_reports": previous_reports,
            "coverage": {
                "observed": sum(item["value"] is not None for item in indicators),
                "total": len(indicators),
                "unavailable": unavailable,
                "real_estate_series": len(real_estate),
                "previous_reports": [item["report_date"] for item in previous_reports],
            },
        }

    def collect_real_estate(self, report_date: date) -> list[dict[str, Any]]:
        """수도권 중심 부동산 시계열을 지표 카드와 같은 모양(최근값·직전 변화·전년 대비)으로 정리한다."""
        metrics = {
            str(row["metric_id"]): row
            for row in self._rows(
                """
                SELECT metric_id, name_ko, category, deal_type, unit, frequency,
                       higher_is, interpretation_ko
                FROM market.real_estate_metrics FINAL
                """
            )
        }
        start = report_date - timedelta(days=REAL_ESTATE_LOOKBACK_DAYS)
        observations = self._rows(
            f"""
            SELECT metric_id, region, region_tier, property_type, deal_type, unit,
                   toString(period) AS observed_on, value
            FROM market.real_estate_observations FINAL
            WHERE period BETWEEN toDate('{start.isoformat()}') AND toDate('{report_date.isoformat()}')
              AND region IN ({_sql_list(REAL_ESTATE_REGIONS)})
              AND property_type IN ({_sql_list(REAL_ESTATE_PROPERTY_TYPES)})
            ORDER BY metric_id, region, property_type, deal_type, period
            """
        )
        grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in observations:
            key = (
                str(row["metric_id"]),
                str(row["region"]),
                str(row["property_type"]),
                str(row["deal_type"]),
            )
            grouped[key].append(row)

        series: list[dict[str, Any]] = []
        for (metric_id, region, property_type, deal_type), history in grouped.items():
            latest = history[-1]
            unit = latest.get("unit") or metrics.get(metric_id, {}).get("unit")
            value = float(latest["value"])
            previous = float(history[-2]["value"]) if len(history) > 1 else None
            # 월간 시계열이므로 13번째 뒤 관측이 전년 동월이다. 주간 지표는 표본이 없으면 생략된다.
            year_ago = float(history[-13]["value"]) if len(history) > 12 else None
            change = value - previous if previous is not None else None
            change_pct = change / abs(previous) if change is not None and previous else None
            yoy_pct = (value - year_ago) / abs(year_ago) if year_ago else None
            definition = metrics.get(metric_id, {})
            series.append(
                {
                    "metric_id": metric_id,
                    "name_ko": definition.get("name_ko") or metric_id,
                    "category": definition.get("category"),
                    "higher_is": definition.get("higher_is"),
                    "interpretation_ko": definition.get("interpretation_ko"),
                    "region": region,
                    "region_tier": latest.get("region_tier"),
                    "property_type": property_type,
                    "deal_type": deal_type,
                    "unit": unit,
                    "period": latest.get("observed_on"),
                    "value": value,
                    "value_display": display_number(value, unit),
                    "change": change,
                    "change_display": display_number(change, unit, signed=True),
                    "change_pct_display": display_percent(change_pct),
                    "yoy_pct_display": display_percent(yoy_pct),
                    "points": len(history),
                }
            )
        series.sort(key=lambda item: (item["metric_id"], item["region"], item["property_type"]))
        return series

    def previous_reports(self, report_date: date, limit: int = PREVIOUS_REPORT_LIMIT) -> list[dict[str, Any]]:
        """직전 리포트들이 남긴 전망·관찰 포인트만 회수해 이번 리포트가 그 연장선에서 쓰이게 한다."""
        rows = self._rows(
            f"""
            SELECT toString(report_date) AS report_day, title, status, markdown
            FROM market.daily_research_reports FINAL
            WHERE report_date < toDate('{report_date.isoformat()}')
            ORDER BY report_date DESC, generated_at DESC
            LIMIT {max(limit, 1) * 4}
            """
        )
        seen: set[str] = set()
        reports: list[dict[str, Any]] = []
        for row in rows:
            day = str(row["report_day"])
            # 같은 날짜를 여러 번 생성했다면 가장 최근 실행만 남긴다.
            if day in seen:
                continue
            seen.add(day)
            reports.append(
                {
                    "report_date": day,
                    "title": row.get("title"),
                    "status": row.get("status"),
                    "sections": extract_forward_sections(str(row.get("markdown") or "")),
                }
            )
            if len(reports) >= limit:
                break
        return reports

    def save_report(self, report: DailyResearchReport) -> int:
        return self.store.insert_records("market.daily_research_reports", [report])

    def latest_report(self) -> dict[str, Any] | None:
        rows = self._rows(
            """
            SELECT toString(report_date) AS report_date, toString(report_id) AS report_id,
                   model, status, title, markdown, summary_json, prompt_version,
                   toString(generated_at) AS generated_at
            FROM market.daily_research_reports FINAL
            ORDER BY report_date DESC, generated_at DESC
            LIMIT 1
            """
        )
        return rows[0] if rows else None

    def _rows(self, sql: str) -> list[dict[str, Any]]:
        result = self.client.query(sql)
        return [
            {column: _json_value(value) for column, value in zip(result.column_names, row, strict=False)}
            for row in result.result_rows
        ]


def extract_forward_sections(markdown: str) -> dict[str, str]:
    """이전 리포트에서 결론·포지션·체크리스트처럼 '다음에 확인할 것'을 담은 섹션만 뽑는다."""
    sections: dict[str, list[str]] = {}
    heading: str | None = None
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            title = stripped[3:].strip()
            heading = title if any(word in title for word in PREVIOUS_REPORT_SECTION_KEYWORDS) else None
            if heading is not None:
                sections.setdefault(heading, [])
            continue
        if stripped.startswith("# "):
            heading = None
            continue
        if heading is not None and stripped:
            sections[heading].append(stripped)
    return {title: "\n".join(body)[:PREVIOUS_REPORT_SECTION_CHARS] for title, body in sections.items() if body}


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)


def display_number(value: float | None, unit: str | None = None, *, signed: bool = False) -> str | None:
    """Format one observation the way it should be read in a report."""
    if value is None:
        return None
    unit_text = (unit or "").strip()
    magnitude = abs(value)
    money = unit_text.upper().startswith(("USD", "KRW")) and " " not in unit_text
    if money and magnitude >= 1_000_000_000_000:
        text = f"{value / 1_000_000_000_000:,.2f}조"
    elif money and magnitude >= 1_000_000_000:
        text = f"{value / 1_000_000_000:,.2f}B"
    elif money and magnitude >= 1_000_000:
        text = f"{value / 1_000_000:,.2f}M"
    elif magnitude >= 1000:
        text = f"{value:,.0f}"
    elif unit_text in {"%", "%p", "ratio"} and magnitude >= 0.01:
        text = f"{value:,.2f}"
    elif magnitude >= 10:
        text = f"{value:,.2f}"
    elif magnitude >= 0.01:
        text = f"{value:,.3f}"
    elif magnitude > 0:
        text = f"{value:.4g}"
    else:
        text = "0"
    text = text.rstrip("0").rstrip(".") if "." in text and "e" not in text else text
    if signed and value > 0:
        text = f"+{text}"
    return f"{text} {unit_text}".strip() if unit_text else text


def display_percent(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value * 100:+.2f}%"


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
