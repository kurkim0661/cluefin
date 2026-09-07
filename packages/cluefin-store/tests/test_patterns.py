import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from cluefin_store.models import DailyOhlcv, PatternEvent
from cluefin_store.patterns import (
    VWAP_PERIOD,
    approximate_volume_profile,
    compute_technical_features,
    detect_pattern_candidates,
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


def make_hlc_candle(index: int, high: str, low: str, close: str, volume: int = 1000) -> DailyOhlcv:
    close_price = Decimal(close)
    return DailyOhlcv(
        trade_date=date(2026, 1, 1) + timedelta(days=index),
        provider="test",
        symbol="005930",
        open=close_price,
        high=Decimal(high),
        low=Decimal(low),
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


def test_compute_technical_features_calculates_vwap_ema_and_rsi() -> None:
    candles = [make_candle(i, str(100 + i), volume=1000 + i) for i in range(60)]

    features = compute_technical_features(candles, provider="test", run_id=RUN_ID, collected_at=COLLECTED_AT)

    # 20일 거래량 가중 평균이라 상승 구간에서는 종가(159)보다 낮다. 하루치 VWAP이면 종가와 같아진다.
    assert features[-1].vwap == Decimal("149.5317")
    assert features[-1].vwap < features[-1].close
    # 창이 다 차기 전에는 값을 내지 않는다.
    assert features[VWAP_PERIOD - 2].vwap is None
    assert features[VWAP_PERIOD - 1].vwap is not None
    assert features[-1].ema_20 is not None
    assert features[-1].ema_50 is not None
    assert features[-1].ema_20 > features[-1].ema_50
    assert features[-1].rsi_14 == 100.0
    assert features[-1].rsi_divergence == "none"


def test_compute_technical_features_detects_bullish_rsi_divergence() -> None:
    closes = [
        "100",
        "101",
        "102",
        "103",
        "104",
        "105",
        "106",
        "107",
        "108",
        "109",
        "110",
        "111",
        "112",
        "113",
        "114",
        "108",
        "102",
        "96",
        "92",
        "98",
        "104",
        "110",
        "108",
        "104",
        "100",
        "96",
        "90",
        "94",
        "100",
        "106",
    ]
    candles = [make_candle(i, close) for i, close in enumerate(closes)]

    features = compute_technical_features(candles, provider="test", run_id=RUN_ID, collected_at=COLLECTED_AT)

    assert any(feature.rsi_divergence == "bullish" for feature in features)


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
    closes = ["120", "112", "100", "108", "115", "101", "109", "118"]
    candles = [make_candle(i, close) for i, close in enumerate(closes)]

    events = detect_pattern_events(candles, provider="test", run_id=RUN_ID, collected_at=COLLECTED_AT)

    assert any(event.pattern_name == "double_bottom" for event in events)
    event = next(event for event in events if event.pattern_name == "double_bottom")
    assert event.target_price is not None
    assert event.support_price == Decimal("98.0000")
    features = json.loads(event.feature_json)
    assert features["geometry"]["points"][0]["role"] == "first_bottom"
    assert features["geometry"]["points"][1]["role"] == "second_bottom"
    assert features["geometry"]["levels"]["neckline"] == "117.0000"


def test_detect_pattern_events_does_not_repeat_same_double_bottom_after_breakout() -> None:
    closes = ["120", "112", "100", "108", "115", "101", "109", "118", "122"]
    candles = [make_candle(i, close) for i, close in enumerate(closes)]

    events = detect_pattern_events(candles, provider="test", run_id=RUN_ID, collected_at=COLLECTED_AT)

    assert not any(event.pattern_name == "double_bottom" for event in events)


def test_detect_pattern_candidates_reports_forming_double_bottom_completion() -> None:
    closes = ["120", "112", "100", "108", "115", "101", "109", "115"]
    candles = [make_candle(i, close) for i, close in enumerate(closes)]

    candidates = detect_pattern_candidates(candles)

    candidate = next(item for item in candidates if item.pattern_name == "double_bottom")
    features = json.loads(candidate.feature_json)
    assert candidate.trigger_price == Decimal("117.0000")
    assert candidate.completion_score > 0.80
    assert features["missing_condition"] == "close_above_neckline"
    assert features["geometry"]["points"][-1]["role"] == "forming"


def test_detect_pattern_events_records_inverse_head_and_shoulders_geometry() -> None:
    closes = ["120", "110", "100", "112", "118", "86", "108", "116", "102", "113", "121"]
    candles = [make_candle(i, close) for i, close in enumerate(closes)]

    events = detect_pattern_events(candles, provider="test", run_id=RUN_ID, collected_at=COLLECTED_AT)

    event = next(event for event in events if event.pattern_name == "inverse_head_and_shoulders")
    features = json.loads(event.feature_json)
    roles = [point["role"] for point in features["geometry"]["points"]]
    assert roles == ["left_shoulder", "head", "right_shoulder"]
    assert features["geometry"]["levels"]["neckline"] == "120.0000"


def test_detect_pattern_events_records_rising_wedge_breakdown_geometry() -> None:
    candles = [
        make_hlc_candle(0, "112", "90", "100"),
        make_hlc_candle(1, "118", "98", "110"),
        make_hlc_candle(2, "121", "103", "113"),
        make_hlc_candle(3, "125", "108", "119"),
        make_hlc_candle(4, "128", "113", "122"),
        make_hlc_candle(5, "131", "118", "126"),
        make_hlc_candle(6, "133", "122", "128"),
        make_hlc_candle(7, "135", "126", "132"),
        make_hlc_candle(8, "136", "129", "133"),
        make_hlc_candle(9, "135", "128", "127"),
    ]

    events = detect_pattern_events(candles, provider="test", run_id=RUN_ID, collected_at=COLLECTED_AT)

    event = next(event for event in events if event.pattern_name == "rising_wedge")
    features = json.loads(event.feature_json)
    assert event.direction == "bearish"
    assert features["geometry"]["kind"] == "rising_wedge"
    assert [point["role"] for point in features["geometry"]["points"]] == [
        "lower_start",
        "upper_start",
        "lower_end",
        "upper_end",
        "breakdown",
    ]
    assert features["geometry"]["levels"]["support"] == "129.0000"
    assert event.target_price == Decimal("122.0000")


def test_detect_pattern_events_records_falling_wedge_breakout_geometry() -> None:
    candles = [
        make_hlc_candle(0, "140", "110", "125"),
        make_hlc_candle(1, "134", "105", "116"),
        make_hlc_candle(2, "129", "101", "111"),
        make_hlc_candle(3, "124", "98", "106"),
        make_hlc_candle(4, "120", "96", "103"),
        make_hlc_candle(5, "116", "94", "100"),
        make_hlc_candle(6, "113", "93", "98"),
        make_hlc_candle(7, "111", "92", "96"),
        make_hlc_candle(8, "109", "91", "95"),
        make_hlc_candle(9, "112", "94", "113"),
    ]

    events = detect_pattern_events(candles, provider="test", run_id=RUN_ID, collected_at=COLLECTED_AT)

    event = next(event for event in events if event.pattern_name == "falling_wedge")
    features = json.loads(event.feature_json)
    assert event.direction == "bullish"
    assert features["geometry"]["kind"] == "falling_wedge"
    assert [point["role"] for point in features["geometry"]["points"]] == [
        "upper_start",
        "lower_start",
        "upper_end",
        "lower_end",
        "breakout",
    ]
    assert features["geometry"]["levels"]["resistance"] == "109.0000"
    assert event.target_price == Decimal("127.0000")


def test_detect_pattern_events_records_ascending_triangle_geometry() -> None:
    candles = [
        make_hlc_candle(0, "118", "90", "100"),
        make_hlc_candle(1, "120", "94", "112"),
        make_hlc_candle(2, "119", "98", "110"),
        make_hlc_candle(3, "120", "102", "116"),
        make_hlc_candle(4, "119", "106", "113"),
        make_hlc_candle(5, "120", "110", "117"),
        make_hlc_candle(6, "119", "113", "116"),
        make_hlc_candle(7, "121", "115", "122"),
    ]

    events = detect_pattern_events(candles, provider="test", run_id=RUN_ID, collected_at=COLLECTED_AT)

    event = next(event for event in events if event.pattern_name == "ascending_triangle")
    features = json.loads(event.feature_json)
    assert event.direction == "bullish"
    assert features["geometry"]["points"][0]["role"] == "upper_start"
    assert features["geometry"]["points"][1]["role"] == "lower_start"
    assert features["geometry"]["points"][2]["role"] == "upper_end"
    assert features["geometry"]["points"][3]["role"] == "lower_end"
    assert features["geometry"]["segments"] == [
        ["upper_start", "upper_end"],
        ["lower_start", "lower_end"],
        ["upper_end", "breakout"],
    ]
    assert features["geometry"]["levels"]["resistance"] == "120.0000"


def test_detect_pattern_events_records_descending_triangle_geometry() -> None:
    candles = [
        make_hlc_candle(0, "140", "100", "124"),
        make_hlc_candle(1, "136", "101", "116"),
        make_hlc_candle(2, "132", "100", "118"),
        make_hlc_candle(3, "128", "101", "112"),
        make_hlc_candle(4, "124", "100", "114"),
        make_hlc_candle(5, "120", "101", "108"),
        make_hlc_candle(6, "116", "100", "107"),
        make_hlc_candle(7, "112", "96", "98"),
    ]

    events = detect_pattern_events(candles, provider="test", run_id=RUN_ID, collected_at=COLLECTED_AT)

    event = next(event for event in events if event.pattern_name == "descending_triangle")
    features = json.loads(event.feature_json)
    assert event.direction == "bearish"
    assert features["geometry"]["kind"] == "descending_triangle"
    assert [point["role"] for point in features["geometry"]["points"]] == [
        "upper_start",
        "lower_start",
        "upper_end",
        "lower_end",
        "breakdown",
    ]
    assert features["geometry"]["segments"] == [
        ["upper_start", "upper_end"],
        ["lower_start", "lower_end"],
        ["lower_end", "breakdown"],
    ]
    assert features["geometry"]["levels"]["support"] == "100.0000"


def test_detect_pattern_events_records_symmetrical_triangle_geometry() -> None:
    candles = [
        make_hlc_candle(0, "140", "100", "120"),
        make_hlc_candle(1, "136", "104", "118"),
        make_hlc_candle(2, "132", "107", "121"),
        make_hlc_candle(3, "128", "110", "119"),
        make_hlc_candle(4, "124", "113", "120"),
        make_hlc_candle(5, "121", "115", "118"),
        make_hlc_candle(6, "119", "116", "117"),
        make_hlc_candle(7, "117", "115", "116"),
        make_hlc_candle(8, "116", "114", "115"),
        make_hlc_candle(9, "124", "118", "123"),
    ]

    events = detect_pattern_events(candles, provider="test", run_id=RUN_ID, collected_at=COLLECTED_AT)

    event = next(event for event in events if event.pattern_name == "symmetrical_triangle")
    features = json.loads(event.feature_json)
    assert event.direction == "bullish"
    assert features["geometry"]["kind"] == "symmetrical_triangle"
    assert [point["role"] for point in features["geometry"]["points"]] == [
        "upper_start",
        "lower_start",
        "upper_end",
        "lower_end",
        "breakout",
    ]
    assert features["geometry"]["segments"] == [
        ["upper_start", "upper_end"],
        ["lower_start", "lower_end"],
        ["upper_end", "breakout"],
    ]
    assert event.target_price == Decimal("156.0000")


def test_detect_pattern_candidates_reports_forming_falling_wedge_completion() -> None:
    candles = [
        make_hlc_candle(0, "140", "110", "125"),
        make_hlc_candle(1, "134", "105", "116"),
        make_hlc_candle(2, "129", "101", "111"),
        make_hlc_candle(3, "124", "98", "106"),
        make_hlc_candle(4, "120", "96", "103"),
        make_hlc_candle(5, "116", "94", "100"),
        make_hlc_candle(6, "113", "93", "98"),
        make_hlc_candle(7, "111", "92", "96"),
        make_hlc_candle(8, "109", "91", "95"),
        make_hlc_candle(9, "108", "94", "106"),
    ]

    candidates = detect_pattern_candidates(candles)

    candidate = next(item for item in candidates if item.pattern_name == "falling_wedge")
    features = json.loads(candidate.feature_json)
    assert candidate.trigger_price == Decimal("109.0000")
    assert Decimal("0.80") <= Decimal(str(candidate.completion_score)) < Decimal("1.0")
    assert features["missing_condition"] == "close_above_resistance"
    assert features["geometry"]["segments"] == [["upper_start", "upper_end"], ["lower_start", "lower_end"]]


def test_detect_pattern_candidates_reports_forming_descending_triangle() -> None:
    candles = [
        make_hlc_candle(0, "140", "100", "124"),
        make_hlc_candle(1, "136", "101", "116"),
        make_hlc_candle(2, "132", "100", "118"),
        make_hlc_candle(3, "128", "101", "112"),
        make_hlc_candle(4, "124", "100", "114"),
        make_hlc_candle(5, "120", "101", "108"),
        make_hlc_candle(6, "116", "100", "107"),
        make_hlc_candle(7, "112", "103", "103"),
    ]

    candidates = detect_pattern_candidates(candles)

    candidate = next(item for item in candidates if item.pattern_name == "descending_triangle")
    features = json.loads(candidate.feature_json)
    assert candidate.direction == "bearish"
    assert candidate.trigger_price == Decimal("100.0000")
    assert features["missing_condition"] == "close_below_support"
    assert features["geometry"]["segments"] == [["upper_start", "upper_end"], ["lower_start", "lower_end"]]


def test_detect_pattern_candidates_reports_forming_symmetrical_triangle() -> None:
    candles = [
        make_hlc_candle(0, "140", "100", "120"),
        make_hlc_candle(1, "136", "104", "118"),
        make_hlc_candle(2, "132", "107", "121"),
        make_hlc_candle(3, "128", "110", "119"),
        make_hlc_candle(4, "124", "113", "120"),
        make_hlc_candle(5, "121", "115", "118"),
        make_hlc_candle(6, "119", "116", "117"),
        make_hlc_candle(7, "117", "115", "116"),
        make_hlc_candle(8, "116", "114", "115"),
        make_hlc_candle(9, "115.5", "113", "115.3"),
    ]

    candidates = detect_pattern_candidates(candles)

    candidate = next(item for item in candidates if item.pattern_name == "symmetrical_triangle")
    features = json.loads(candidate.feature_json)
    assert candidate.direction == "bullish"
    assert Decimal("0.60") <= Decimal(str(candidate.completion_score)) < Decimal("1.0")
    assert candidate.trigger_price == Decimal("116.0000")
    assert features["missing_condition"] == "close_above_resistance"
    assert features["geometry"]["segments"] == [["upper_start", "upper_end"], ["lower_start", "lower_end"]]


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


def test_label_pattern_outcome_handles_bearish_target_and_stop() -> None:
    event = PatternEvent(
        trade_date=date(2026, 1, 5),
        provider="test",
        symbol="005930",
        pattern_name="rising_wedge",
        pattern_category="bearish_reversal",
        direction="bearish",
        detection_date=date(2026, 1, 5),
        breakout_date=date(2026, 1, 5),
        retest_date=None,
        neckline_price=None,
        support_price=Decimal("129.0000"),
        resistance_price=Decimal("136.0000"),
        stop_price=Decimal("136.0000"),
        target_price=Decimal("122.0000"),
        confidence_score=0.64,
        feature_json="{}",
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
    )
    future = [
        make_hlc_candle(5, "130", "126", "127"),
        make_hlc_candle(6, "128", "121", "123"),
        make_hlc_candle(7, "127", "120", "121"),
    ]

    outcome = label_pattern_outcome(event, future, horizon_days=3, run_id=RUN_ID, collected_at=COLLECTED_AT)

    assert outcome.entry_price == Decimal("129.0000")
    assert outcome.target_hit == 1
    assert outcome.stop_hit == 0
    assert outcome.return_at_horizon is not None
    assert outcome.return_at_horizon > 0


def test_detect_pattern_events_does_not_crash_when_high_tight_flag_peak_is_last_candle() -> None:
    candles = [make_candle(index, "100") for index in range(19)]
    candles.append(
        DailyOhlcv(
            trade_date=date(2026, 1, 20),
            provider="test",
            symbol="005930",
            open=Decimal("180"),
            high=Decimal("210"),
            low=Decimal("170"),
            close=Decimal("200"),
            volume=1000,
            trading_amount=Decimal("200000"),
            run_id=RUN_ID,
            collected_at=COLLECTED_AT,
        )
    )

    events = detect_pattern_events(candles, provider="test", run_id=RUN_ID, collected_at=COLLECTED_AT)

    assert isinstance(events, list)
