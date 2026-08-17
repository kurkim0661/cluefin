import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from cluefin_store.analysis import (
    PatternAnalysisConfig,
    build_pattern_analysis_dataset,
    collect_and_store_pattern_analysis,
    required_collection_window,
)
from cluefin_store.models import DailyOhlcv

RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
COLLECTED_AT = datetime(2026, 8, 14, 16, 0, 0)


def make_candle(index: int, close: str, *, symbol: str = "005930", volume: int = 1000) -> DailyOhlcv:
    close_price = Decimal(close)
    return DailyOhlcv(
        trade_date=date(2026, 1, 1) + timedelta(days=index),
        provider="test",
        symbol=symbol,
        open=close_price - Decimal("1"),
        high=close_price + Decimal("2"),
        low=close_price - Decimal("2"),
        close=close_price,
        volume=volume,
        trading_amount=close_price * volume,
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
    )


def test_required_collection_window_extends_for_warmup_and_outcomes() -> None:
    config = PatternAnalysisConfig(warmup_calendar_days=400, outcome_horizons=(5, 20))

    start, end = required_collection_window(
        target_start=date(2026, 8, 1),
        target_end=date(2026, 8, 31),
        config=config,
    )

    assert start == date(2025, 6, 27)
    assert end == date(2026, 9, 20)


def test_build_pattern_analysis_dataset_scans_history_and_labels_outcomes() -> None:
    closes = ["120", "112", "100", "108", "115", "101", "109", "118", "122", "124", "136", "138"]
    candles = [make_candle(index, close) for index, close in enumerate(closes)]
    config = PatternAnalysisConfig(
        target_start=date(2026, 1, 1),
        target_end=date(2026, 1, 12),
        pattern_lookback_days=9,
        volume_profile_lookback_days=3,
        outcome_horizons=(3,),
        retest_window_days=2,
    )

    dataset = build_pattern_analysis_dataset(
        {"005930": candles},
        provider="test",
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
        config=config,
    )

    assert dataset.daily_ohlcv == candles
    assert len(dataset.technical_features) == len(candles)
    assert dataset.volume_profile_levels
    assert any(event.pattern_name == "double_bottom" for event in dataset.pattern_events)
    event = next(event for event in dataset.pattern_events if event.pattern_name == "double_bottom")
    assert event.htf_trend in {"bullish", "bearish", "sideways", "unknown"}
    assert event.confluence_score >= event.confidence_score
    assert event.retest_confirmed in {0, 1}
    double_bottom_outcomes = [
        outcome for outcome in dataset.pattern_outcomes if outcome.pattern_name == "double_bottom"
    ]
    assert double_bottom_outcomes
    assert any(outcome.target_hit == 1 for outcome in double_bottom_outcomes)


def test_build_pattern_analysis_dataset_scores_prior_trend_and_breakout_volume() -> None:
    closes = ["160", "148", "132", "120", "128", "135", "121", "129", "138", "142", "146"]
    volumes = [1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 4500, 1200, 1200]
    candles = [make_candle(index, close, volume=volumes[index]) for index, close in enumerate(closes)]
    config = PatternAnalysisConfig(
        target_start=date(2026, 1, 1),
        target_end=date(2026, 1, 11),
        pattern_lookback_days=9,
        volume_profile_lookback_days=5,
        outcome_horizons=(2,),
        retest_window_days=1,
    )

    dataset = build_pattern_analysis_dataset(
        {"005930": candles},
        provider="test",
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
        config=config,
    )

    event = next(event for event in dataset.pattern_events if event.pattern_name == "double_bottom")
    features = json.loads(event.feature_json)
    assert features["prior_trend"] == "bearish"
    assert features["pattern_context"] == "aligned_reversal"
    assert features["volume_confirmation"] is True
    assert Decimal(features["breakout_volume_ratio"]) >= Decimal("2.0")
    assert features["score_components"]["trend_context"] > 0
    assert features["score_components"]["volume_confirmation"] > 0
    assert event.confluence_score > event.confidence_score + 0.15


class FakeProvider:
    provider_name = "fake"

    def __init__(self, candles: list[DailyOhlcv]) -> None:
        self.candles = candles
        self.calls: list[tuple[tuple[str, ...], date, date]] = []

    def fetch_daily_ohlcv(
        self, symbols: tuple[str, ...], start_date: date, end_date: date
    ) -> dict[str, list[DailyOhlcv]]:
        self.calls.append((symbols, start_date, end_date))
        return {symbol: [candle for candle in self.candles if candle.symbol == symbol] for symbol in symbols}


class FakeStore:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, int]] = []

    def insert_records(self, table: str, records: list) -> int:
        self.inserts.append((table, len(records)))
        return len(records)


def test_collect_and_store_pattern_analysis_fetches_required_window_and_inserts_tables() -> None:
    candles = [make_candle(index, str(100 + index)) for index in range(12)]
    provider = FakeProvider(candles)
    store = FakeStore()
    config = PatternAnalysisConfig(warmup_calendar_days=30, outcome_horizons=(5,))

    summary = collect_and_store_pattern_analysis(
        provider=provider,
        store=store,
        symbols=("005930",),
        target_start=date(2026, 1, 10),
        target_end=date(2026, 1, 12),
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
        config=config,
    )

    assert provider.calls == [((("005930",)), date(2025, 12, 11), date(2026, 1, 17))]
    assert summary["market.daily_ohlcv"] == 12
    assert any(table == "market.daily_pattern_events" for table, _ in store.inserts)
