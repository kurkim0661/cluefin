from decimal import Decimal

import pytest

from cluefin_web.repository import (
    DashboardRepository,
    _build_market_pulse,
    _indicator_votes_from_signal_row,
    _strategy_config_from_payload,
)


class FakeQueryResult:
    def __init__(self, rows: list[tuple], columns: list[str]) -> None:
        self.result_rows = rows
        self.column_names = columns


class FakeClient:
    def query(self, sql: str) -> FakeQueryResult:
        if "countDistinct(symbol)" in sql:
            return FakeQueryResult([(50, 3915, 11309, "2026-08-16")], ["symbols", "events", "outcomes", "as_of"])
        if "pattern_performance_summary" in sql:
            return FakeQueryResult(
                [("double_bottom", 20, "bullish", 1, 1, 120, 0.82, 0.11, 0.72)],
                [
                    "pattern_name",
                    "horizon_days",
                    "htf_trend",
                    "volume_profile_confluence",
                    "retest_confirmed",
                    "sample_count",
                    "target_hit_rate",
                    "avg_return_at_horizon",
                    "avg_confluence_score",
                ],
            )
        if "daily_pattern_events" in sql and "LEFT JOIN market.daily_universe_members" in sql:
            return FakeQueryResult(
                [("005930", "삼성전자", "double_bottom", "2026-08-14", 0.86, "bullish", 1, 1)],
                [
                    "symbol",
                    "name",
                    "pattern_name",
                    "detection_date",
                    "confluence_score",
                    "htf_trend",
                    "volume_profile_confluence",
                    "retest_confirmed",
                ],
            )
        if "FROM market.daily_universe_members" in sql:
            if "WHERE m.trade_date" not in sql:
                return FakeQueryResult([], [])
            return FakeQueryResult(
                [
                    (
                        "2026-08-16",
                        "toss",
                        "kr_market_trading_amount_top50_1y",
                        "KR",
                        1,
                        "005930",
                        "삼성전자",
                        "market_trading_amount",
                        123456,
                    )
                ],
                [
                    "trade_date",
                    "provider",
                    "universe_name",
                    "market_country",
                    "rank",
                    "symbol",
                    "name",
                    "selection_metric_name",
                    "selection_metric_value",
                ],
            )
        if "FROM market.daily_ohlcv" in sql and "111111" in sql:
            rows = [
                ("2026-08-07", "111111", 125, 140, 110, 125, 1000, 125000, None, None, None, None, None, "none"),
                ("2026-08-08", "111111", 116, 134, 105, 116, 1000, 116000, None, None, None, None, None, "none"),
                ("2026-08-09", "111111", 111, 129, 101, 111, 1000, 111000, None, None, None, None, None, "none"),
                ("2026-08-10", "111111", 106, 124, 98, 106, 1000, 106000, None, None, None, None, None, "none"),
                ("2026-08-11", "111111", 103, 120, 96, 103, 1000, 103000, None, None, None, None, None, "none"),
                ("2026-08-12", "111111", 100, 116, 94, 100, 1000, 100000, None, None, None, None, None, "none"),
                ("2026-08-13", "111111", 98, 113, 93, 98, 1000, 98000, None, None, None, None, None, "none"),
                ("2026-08-14", "111111", 96, 111, 92, 96, 1000, 96000, None, None, None, None, None, "none"),
                ("2026-08-15", "111111", 95, 109, 91, 95, 1000, 95000, None, None, None, None, None, "none"),
                ("2026-08-16", "111111", 106, 108, 94, 106, 1000, 106000, None, None, None, None, None, "none"),
            ]
            return FakeQueryResult(
                rows,
                [
                    "trade_date",
                    "symbol",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "trading_amount",
                    "vwap",
                    "ema_20",
                    "ema_50",
                    "ema_200",
                    "rsi_14",
                    "rsi_divergence",
                ],
            )
        if "FROM market.daily_ohlcv" in sql:
            return FakeQueryResult(
                [
                    (
                        "2026-08-16",
                        "005930",
                        69000,
                        71000,
                        68000,
                        70000,
                        1000,
                        70000000,
                        70000,
                        69900,
                        69000,
                        65000,
                        61.5,
                        "bullish",
                    )
                ],
                [
                    "trade_date",
                    "symbol",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "trading_amount",
                    "vwap",
                    "ema_20",
                    "ema_50",
                    "ema_200",
                    "rsi_14",
                    "rsi_divergence",
                ],
            )
        if "FROM market.daily_technical_features" in sql:
            return FakeQueryResult(
                [("2026-08-16", "005930", 70000, 69900, 69000, 65000, 61.5, "bullish")],
                [
                    "trade_date",
                    "symbol",
                    "vwap",
                    "ema_20",
                    "ema_50",
                    "ema_200",
                    "rsi_14",
                    "rsi_divergence",
                ],
            )
        if "FROM market.daily_volume_profile_levels" in sql:
            return FakeQueryResult(
                [("2026-08-16", 60, 70000, 68000, 72000, '[{"price":"70000.0000","volume":1000}]')],
                ["trade_date", "lookback_days", "poc_price", "value_area_low", "value_area_high", "bins_json"],
            )
        if "FROM market.symbol_sentiment_summary" in sql:
            return FakeQueryResult(
                [("005930", 3, 0.35, "bullish", "긍정 키워드 감지: 수주", "2026-08-17 09:00:00")],
                ["symbol", "item_count", "avg_sentiment_score", "sentiment_label", "latest_reason", "latest_at"],
            )
        if "FROM market.symbol_sentiment_items" in sql:
            return FakeQueryResult(
                [
                    (
                        "삼성전자 수주 확대",
                        "Example",
                        "https://example.com/news",
                        "2026-08-17 09:00:00",
                        "계약 소식",
                        "bullish",
                        0.7,
                        "긍정 키워드 감지: 수주, 계약",
                    )
                ],
                [
                    "title",
                    "source",
                    "url",
                    "published_at",
                    "summary",
                    "sentiment_label",
                    "sentiment_score",
                    "sentiment_reason",
                ],
            )
        if "FROM market.daily_pattern_events" in sql:
            return FakeQueryResult(
                [
                    ("double_bottom", "2026-08-16", 69000, 68000, 71000, 73000, 0.86, '{"low_indexes":[1,5]}'),
                    ("double_bottom", "2026-08-15", 69000, 68000, 71000, 73000, 0.82, '{"low_indexes":[1,5]}'),
                ],
                [
                    "pattern_name",
                    "detection_date",
                    "neckline_price",
                    "support_price",
                    "resistance_price",
                    "target_price",
                    "confluence_score",
                    "feature_json",
                ],
            )
        return FakeQueryResult([], [])


def test_repository_snapshot_returns_dashboard_sections() -> None:
    repository = DashboardRepository(FakeClient())

    snapshot = repository.snapshot()

    assert snapshot["overview"]["symbols"] == 50
    assert snapshot["performance"][0]["pattern_name"] == "double_bottom"
    assert snapshot["signals"][0]["symbol"] == "005930"
    assert snapshot["universe"][0]["name"] == "삼성전자"
    assert snapshot["technicals"][0]["rsi_divergence"] == "bullish"
    assert snapshot["sentiment"][0]["sentiment_label"] == "bullish"


def test_repository_symbol_chart_returns_candles_indicators_profiles_and_patterns() -> None:
    repository = DashboardRepository(FakeClient())

    chart = repository.symbol_chart("005930")

    assert chart["symbol"] == "005930"
    assert chart["candles"][0]["close"] == 70000
    assert chart["candles"][0]["ema_20"] == 69900
    assert chart["volume_profile"][0]["poc_price"] == 70000
    assert chart["volume_profile"][0]["bins"][0]["volume"] == 1000
    assert chart["patterns"][0]["pattern_name"] == "double_bottom"
    assert len(chart["patterns"]) == 1
    assert chart["sentiment_items"][0]["url"] == "https://example.com/news"
    assert chart["sentiment_items"][0]["sentiment_reason"] == "긍정 키워드 감지: 수주, 계약"


def test_repository_symbol_chart_returns_forming_pattern_candidates() -> None:
    repository = DashboardRepository(FakeClient())

    chart = repository.symbol_chart("111111")

    candidate = next(item for item in chart["pattern_candidates"] if item["pattern_name"] == "falling_wedge")
    assert candidate["completion_score"] >= 0.8
    assert candidate["trigger_price"] == Decimal("109.0000")
    assert candidate["features"]["missing_condition"] == "close_above_resistance"


def test_strategy_config_from_payload_keeps_rule_builder_lists() -> None:
    config = _strategy_config_from_payload(
        {
            "min_confluence": "0.75",
            "position_pct": "0.25",
            "max_positions": "4",
            "hold_days": "15",
            "pattern_names": ["inverse_head_and_shoulders", "double_bottom"],
            "selected_indicators": ["volume_profile", "vwap", "ema"],
            "min_indicator_agreement": "3",
            "require_retest": "on",
        }
    )

    assert config["pattern_names"] == ["inverse_head_and_shoulders", "double_bottom"]
    assert config["selected_indicators"] == ["volume_profile", "vwap", "ema"]
    assert config["min_indicator_agreement"] == 3
    assert config["require_retest"] is True


def test_indicator_votes_from_signal_row_uses_technical_and_event_context() -> None:
    votes = _indicator_votes_from_signal_row(
        {
            "direction": "bullish",
            "htf_trend": "bullish",
            "volume_profile_confluence": 1,
            "retest_confirmed": 1,
            "close": Decimal("110"),
            "vwap": Decimal("100"),
            "ema_20": Decimal("108"),
            "ema_50": Decimal("102"),
            "rsi_divergence": "bullish",
            "feature_json": '{"breakout_volume_ratio":"2.1000"}',
        }
    )

    assert votes["volume_profile"] == "bullish"
    assert votes["vwap"] == "bullish"
    assert votes["ema"] == "bullish"
    assert votes["rsi_divergence"] == "bullish"
    assert votes["volume"] == "bullish"
    assert votes["htf_trend"] == "bullish"
    assert votes["retest"] == "bullish"


def test_market_pulse_builds_regime_sections_and_missing_connections() -> None:
    definitions = [
        {
            "indicator_id": "us_real_yield_10y",
            "name_ko": "미국 10년 실질금리",
            "domain": "global",
            "category": "rates",
            "provider": "fred",
            "unit": "%",
            "frequency": "daily",
            "higher_is": "risk_off",
            "importance": 3,
            "availability": "public",
        },
        {
            "indicator_id": "btc_spot_etf_flow",
            "name_ko": "비트코인 현물 ETF 순유입",
            "domain": "crypto",
            "category": "flows",
            "provider": "licensed",
            "unit": "USD",
            "frequency": "daily",
            "higher_is": "risk_on",
            "importance": 3,
            "availability": "licensed",
        },
    ]
    observations = [
        {"indicator_id": "us_real_yield_10y", "period": "2026-08-25", "value": 2.4, "provider": "fred"},
        {"indicator_id": "us_real_yield_10y", "period": "2026-08-26", "value": 2.3, "provider": "fred"},
    ]

    pulse = _build_market_pulse(definitions, observations)

    assert pulse["regime"]["key"] == "supportive"
    assert pulse["coverage"]["observed"] == 1
    assert pulse["coverage"]["connection_required"] == 1
    assert pulse["sections"]["rates_liquidity"]["tone"] == "positive"
    real_yield = next(item for item in pulse["indicators"] if item["indicator_id"] == "us_real_yield_10y")
    assert real_yield["impact_score"] > 0
    assert real_yield["impact_label"] == "우호적"
    assert "하락" in real_yield["impact_explanation"]
    assert real_yield["impact_confidence"] == "low"
    assert real_yield["level_label"].startswith("현재 수준")
    assert pulse["unavailable"][0]["indicator_id"] == "btc_spot_etf_flow"


class FakeReportClient:
    def __init__(self) -> None:
        self.parameters: dict | None = None

    def query(self, sql: str, parameters: dict | None = None) -> FakeQueryResult:
        self.parameters = parameters
        if "length(markdown)" in sql:
            return FakeQueryResult(
                [
                    (
                        "2026-08-27",
                        "report-2",
                        "gpt",
                        "validated",
                        "오늘의 시장 리포트",
                        "cluefin-daily-v1",
                        "2026-08-27 09:10:00",
                        2400,
                    ),
                    (
                        "2026-08-27",
                        "report-1",
                        "gpt",
                        "partial",
                        "오늘의 시장 리포트",
                        "cluefin-daily-v1",
                        "2026-08-27 08:00:00",
                        1200,
                    ),
                ],
                [
                    "report_date",
                    "report_id",
                    "model",
                    "status",
                    "title",
                    "prompt_version",
                    "generated_at",
                    "markdown_chars",
                ],
            )
        return FakeQueryResult(
            [
                (
                    "2026-08-27",
                    "report-1",
                    "gpt",
                    "partial",
                    "제목",
                    "# 본문",
                    "{}",
                    "cluefin-daily-v1",
                    "2026-08-27 08:00:00",
                )
            ],
            [
                "report_date",
                "report_id",
                "model",
                "status",
                "title",
                "markdown",
                "summary_json",
                "prompt_version",
                "generated_at",
            ],
        )


def test_research_report_history_keeps_every_stored_run() -> None:
    repository = DashboardRepository(FakeReportClient())

    history = repository.research_report_history(limit=10)

    assert [row["report_id"] for row in history] == ["report-2", "report-1"]
    assert history[0]["markdown_chars"] == 2400


def test_research_report_detail_is_queried_by_parameter() -> None:
    client = FakeReportClient()
    repository = DashboardRepository(client)

    report = repository.research_report("report-1")

    assert report["markdown"] == "# 본문"
    assert client.parameters == {"report_id": "report-1"}


class FakeEstateClient:
    def __init__(self, rows: list[tuple], columns: list[str]) -> None:
        self.rows = rows
        self.columns = columns
        self.sql: str = ""
        self.parameters: dict | None = None

    def query(self, sql: str, parameters: dict | None = None) -> FakeQueryResult:
        self.sql = sql
        self.parameters = parameters
        return FakeQueryResult(self.rows, self.columns)


def _estate_repository(rows, columns=("bucket", "series", "value", "unit")):
    client = FakeEstateClient(list(rows), list(columns))
    return DashboardRepository(client), client


def test_real_estate_query_builds_series_per_dimension_value() -> None:
    repository, client = _estate_repository(
        [
            ("2026-05-01", "서울", 102.7, "2026.01=100"),
            ("2026-05-01", "경기", 101.5, "2026.01=100"),
            ("2026-06-01", "서울", 104.0, "2026.01=100"),
            ("2026-06-01", "경기", 102.3, "2026.01=100"),
        ]
    )

    result = repository.real_estate_query(
        {
            "metric_ids": ["house_sale_price_index"],
            "series_dimension": "region",
            "filters": {"region": ["서울", "경기"], "property_type": ["아파트"]},
            "bucket": "month",
        }
    )

    assert result["buckets"] == ["2026-05-01", "2026-06-01"]
    assert {item["name"]: item["points"] for item in result["series"]} == {
        "서울": [102.7, 104.0],
        "경기": [101.5, 102.3],
    }
    assert "toStartOfMonth(period)" in client.sql
    assert client.parameters["f0"] == ["house_sale_price_index"]
    assert client.parameters["f2"] == ["아파트"]


def test_real_estate_query_supports_change_and_rebase_transforms() -> None:
    rows = [
        ("2026-04-01", "서울", 100.0, "idx"),
        ("2026-05-01", "서울", 102.0, "idx"),
        ("2026-06-01", "서울", 105.06, "idx"),
    ]
    repository, _ = _estate_repository(rows)
    change = repository.real_estate_query(
        {"metric_ids": ["m"], "series_dimension": "region", "bucket": "month", "transform": "change"}
    )
    repository, _ = _estate_repository(rows)
    rebase = repository.real_estate_query(
        {"metric_ids": ["m"], "series_dimension": "region", "bucket": "month", "transform": "rebase"}
    )

    assert change["unit"] == "%"
    assert change["series"][0]["points"] == [None, 2.0, 3.0]
    assert rebase["series"][0]["points"] == [100.0, 102.0, 105.06]


def test_real_estate_query_year_over_year_uses_twelve_month_lag() -> None:
    rows = [(f"2025-{month:02d}-01", "서울", 100.0, "idx") for month in range(1, 13)]
    rows.append(("2026-01-01", "서울", 110.0, "idx"))
    repository, _ = _estate_repository(rows)

    result = repository.real_estate_query(
        {"metric_ids": ["m"], "series_dimension": "region", "bucket": "month", "transform": "yoy"}
    )

    assert result["series"][0]["points"][-1] == 10.0
    assert result["series"][0]["points"][0] is None


def test_real_estate_ratio_transform_divides_two_metrics() -> None:
    repository, client = _estate_repository(
        [
            ("2026-06-01", "서울", 96.0, "idx", "jeonse"),
            ("2026-06-01", "서울", 120.0, "idx", "sale"),
        ],
        columns=("bucket", "series", "value", "unit", "metric_id"),
    )

    result = repository.real_estate_query(
        {
            "metric_ids": ["jeonse", "sale"],
            "series_dimension": "region",
            "bucket": "month",
            "transform": "ratio",
        }
    )

    assert result["unit"] == "%"
    assert result["series"][0]["points"] == [80.0]
    assert "metric_id" in client.sql


def test_real_estate_query_rejects_unknown_options() -> None:
    repository, _ = _estate_repository([])

    for payload in (
        {"metric_ids": [], "bucket": "month"},
        {"metric_ids": ["m"], "bucket": "daily"},
        {"metric_ids": ["m"], "series_dimension": "unknown"},
        {"metric_ids": ["m"], "transform": "unknown"},
        {"metric_ids": ["m"], "transform": "ratio"},
        {"metric_ids": ["m"], "overlay": {"metric_id": "unsold_housing"}},
    ):
        with pytest.raises(ValueError):
            repository.real_estate_query(payload)


class FakeQueuedEstateClient:
    """질의 순서대로 다른 응답을 돌려준다. 본 차트와 기준선 질의를 구분하기 위해 쓴다."""

    def __init__(self, responses: list[tuple[list[tuple], list[str]]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict | None]] = []

    def query(self, sql: str, parameters: dict | None = None) -> FakeQueryResult:
        self.calls.append((sql, parameters))
        rows, columns = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        return FakeQueryResult(list(rows), list(columns))


def _overlay_repository(main_rows, overlay_rows):
    client = FakeQueuedEstateClient(
        [
            (main_rows, ["bucket", "series", "value", "unit"]),
            (overlay_rows, ["bucket", "value", "unit"]),
        ]
    )
    return DashboardRepository(client), client


def test_real_estate_query_overlays_price_index_on_the_same_buckets() -> None:
    repository, client = _overlay_repository(
        [
            ("2026-05-01", "수도권", 1200.0, "호"),
            ("2026-06-01", "수도권", 1500.0, "호"),
        ],
        [
            ("2026-04-01", 99.0, "2026.01=100"),
            ("2026-05-01", 101.2, "2026.01=100"),
            ("2026-06-01", 102.5, "2026.01=100"),
        ],
    )

    result = repository.real_estate_query(
        {
            "metric_ids": ["unsold_housing"],
            "series_dimension": "region",
            "bucket": "month",
            "filters": {"region": ["수도권"]},
            "overlay": {"region": "수도권", "property_type": "아파트"},
        }
    )

    overlay = result["overlay"]
    assert overlay["metric_id"] == "house_sale_price_index"
    assert overlay["label"] == "수도권 아파트"
    assert overlay["unit"] == "2026.01=100"
    # 본 차트 구간(2026-05·06)만 남고 앞선 관측은 잘린다.
    assert overlay["points"] == [101.2, 102.5]
    overlay_sql, overlay_parameters = client.calls[-1]
    assert "avg(value)" in overlay_sql
    assert overlay_parameters["o_metric"] == "house_sale_price_index"
    assert overlay_parameters["o_region"] == "수도권"
    assert overlay_parameters["o_property_type"] == "아파트"
    assert overlay_parameters["o_start"] == "2026-05-01"


def test_real_estate_overlay_defaults_region_to_first_filter_and_can_be_omitted() -> None:
    repository, client = _overlay_repository(
        [("2026-06-01", "경기", 3.0, "%")],
        [("2026-06-01", 98.4, "2026.01=100")],
    )

    result = repository.real_estate_query(
        {
            "metric_ids": ["land_price_change"],
            "series_dimension": "region",
            "bucket": "month",
            "filters": {"region": ["경기", "인천"]},
            "overlay": True,
        }
    )

    assert result["overlay"]["region"] == "경기"
    assert result["overlay"]["property_type"] == "아파트"
    assert client.calls[-1][1]["o_region"] == "경기"

    # 주택유형을 나누지 않는 지표("전체")는 이름에 유형을 붙이지 않는다.
    repository, _ = _overlay_repository([("2026-06-01", "서울", 3.0, "%")], [("2026-06-01", 158.4, "2017.11=100")])
    all_types = repository.real_estate_query(
        {
            "metric_ids": ["land_price_change"],
            "series_dimension": "region",
            "bucket": "month",
            "overlay": {"metric_id": "apartment_real_transaction_index", "region": "수도권", "property_type": "전체"},
        }
    )
    assert all_types["overlay"]["label"] == "수도권"

    repository, client = _overlay_repository([("2026-06-01", "경기", 3.0, "%")], [])
    plain = repository.real_estate_query(
        {"metric_ids": ["land_price_change"], "series_dimension": "region", "bucket": "month"}
    )

    assert plain["overlay"] is None
    assert len(client.calls) == 1


def test_real_estate_overlay_is_dropped_when_no_price_data_overlaps() -> None:
    repository, _ = _overlay_repository(
        [("2026-06-01", "서울", 120.0, "호")],
        [("2026-06-01", None, "2026.01=100")],
    )

    result = repository.real_estate_query(
        {"metric_ids": ["unsold_housing"], "series_dimension": "region", "bucket": "month", "overlay": True}
    )

    assert result["overlay"] is None


def test_real_estate_templates_overlay_price_index_when_level_is_not_visible() -> None:
    from cluefin_web.repository import REAL_ESTATE_OVERLAY_METRICS, REAL_ESTATE_TEMPLATES

    by_id = {template["id"]: template for template in REAL_ESTATE_TEMPLATES}
    assert by_id["unsold_inventory"]["overlay"]["region"] == "수도권"
    assert by_id["unsold_inventory"]["overlay"]["metric_id"] == "apartment_real_transaction_index"
    assert by_id["long_run_cycle"]["overlay"]["metric_id"] == "house_sale_price_index_long"
    # 원값 지수 템플릿은 이미 가격 수준을 그리므로 기준선을 겹치지 않는다.
    assert "overlay" not in by_id["capital_sale_index"]
    for template in REAL_ESTATE_TEMPLATES:
        overlay = template.get("overlay")
        if overlay and overlay.get("metric_id"):
            assert overlay["metric_id"] in REAL_ESTATE_OVERLAY_METRICS
        if template["transform"] != "raw":
            assert overlay, f"{template['id']} 템플릿에 가격 기준선이 없습니다"
