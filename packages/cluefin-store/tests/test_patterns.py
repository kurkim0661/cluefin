from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from cluefin_store.models import DailyOhlcv, PatternEvent
from cluefin_store.patterns import (
    approximate_volume_profile,
    compute_technical_features,
    detect_pattern_events,
    label_pattern_outcome,
)

RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
COLLECTED_AT = datetime(2026, 8, 14, 16, 0, 0)


def make_candle(index: int, close: str, volume: int = 1000) -> DailyOhlcv:
    close_price = Decimal(close)
    return DailyOhlcv(
        trade_date=date(2026, 1, 1) + timedelta(days=index),
        provider="test",
        symbol="005930",
        open=close_price - Decimal("1"),
        high=close_price + Decimal("2"),
        low=close_price - Decimal("2"),
        close=close_price,
        volume=volume,
        trading_amount=close_price * volume,
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
    )


def test_compute_technical_features_calculates_ma_50() -> None:
    candles = [make_candle(i, str(100 + i)) for i in range(60)]

    features = compute_technical_features(candles, provider="test", run_id=RUN_ID, collected_at=COLLECTED_AT)

    assert features[-1].ma_50 == Decimal("134.5000")
    assert features[-1].htf_trend == "bullish"
    assert features[-1].return_5d is not None


def test_approximate_volume_profile_uses_highest_volume_typical_price_as_poc() -> None:
    candles = [
        make_candle(0, "100", volume=100),
        make_candle(1, "110", volume=5000),
        make_candle(2, "120", volume=100),
    ]

    profile = approximate_volume_profile(
        candles,
        provider="test",
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
        lookback_days=3,
        bin_count=3,
    )

    assert profile.poc_price == Decimal("110.0000")
    assert profile.value_area_low <= profile.poc_price <= profile.value_area_high


def test_detect_pattern_events_detects_double_bottom_breakout() -> None:
    closes = ["120", "112", "100", "108", "115", "101", "109", "118", "122"]
    candles = [make_candle(i, close) for i, close in enumerate(closes)]

    events = detect_pattern_events(candles, provider="test", run_id=RUN_ID, collected_at=COLLECTED_AT)

    assert any(event.pattern_name == "double_bottom" for event in events)
    event = next(event for event in events if event.pattern_name == "double_bottom")
    assert event.target_price is not None
    assert event.support_price == Decimal("98.0000")


def test_label_pattern_outcome_marks_target_hit() -> None:
    event = PatternEvent(
        trade_date=date(2026, 1, 5),
        provider="test",
        symbol="005930",
        pattern_name="double_bottom",
        pattern_category="reversal",
        direction="bullish",
        detection_date=date(2026, 1, 5),
        breakout_date=date(2026, 1, 5),
        retest_date=None,
        neckline_price=Decimal("115.0000"),
        support_price=Decimal("100.0000"),
        resistance_price=Decimal("115.0000"),
        stop_price=Decimal("99.0000"),
        target_price=Decimal("130.0000"),
        confidence_score=0.7,
        feature_json="{}",
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
    )
    future = [make_candle(5, "120"), make_candle(6, "131"), make_candle(7, "129")]

    outcome = label_pattern_outcome(event, future, horizon_days=3, run_id=RUN_ID, collected_at=COLLECTED_AT)

    assert outcome.target_hit == 1
    assert outcome.stop_hit == 0
    assert outcome.return_at_horizon is not None
