from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from cluefin_store.db import ClickHouseStore
from cluefin_store.models import DailyResearchReport


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
            indicators.append(
                {
                    **definition,
                    "value": float(latest["value"]) if latest else None,
                    "period": latest.get("observed_on") if latest else None,
                    "provider": latest.get("provider") if latest else None,
                    "change": change,
                    "change_pct": change_pct,
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
            "coverage": {
                "observed": sum(item["value"] is not None for item in indicators),
                "total": len(indicators),
                "unavailable": unavailable,
            },
        }

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


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
