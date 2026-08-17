from decimal import Decimal

from cluefin_web.repository import DashboardRepository, _indicator_votes_from_signal_row, _strategy_config_from_payload


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
