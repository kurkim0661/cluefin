from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from cluefin_store.models import (
    DailyOhlcv,
    PatternEvent,
    PatternOutcome,
    TechnicalFeature,
    VolumeProfileLevel,
    _quantize_price,
)


def _average(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return _quantize_price(sum(values) / Decimal(len(values)))


def _return(current: Decimal, past: Decimal) -> float | None:
    if past == 0:
        return None
    return float((current / past) - Decimal("1"))


def _simple_ma(candles: list[DailyOhlcv], end_index: int, period: int) -> Decimal | None:
    start = end_index - period + 1
    if start < 0:
        return None
    return _average([candle.close for candle in candles[start : end_index + 1]])


def _atr(candles: list[DailyOhlcv], end_index: int, period: int = 14) -> Decimal | None:
    start = end_index - period + 1
    if start < 1:
        return None

    ranges: list[Decimal] = []
    for index in range(start, end_index + 1):
        current = candles[index]
        previous = candles[index - 1]
        true_range = max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        ranges.append(true_range)
    return _average(ranges)


def _weekly_ma(candles: list[DailyOhlcv], end_index: int, period_weeks: int) -> Decimal | None:
    period_days = period_weeks * 5
    return _simple_ma(candles, end_index, period_days)


def _htf_trend(close: Decimal, ma_50: Decimal | None, ma_200: Decimal | None) -> str:
    if ma_50 is None:
        return "unknown"
    if ma_200 is None:
        return "bullish" if close > ma_50 else "bearish"
    if close > ma_50 > ma_200:
        return "bullish"
    if close < ma_50 < ma_200:
        return "bearish"
    return "sideways"


def compute_technical_features(
    candles: list[DailyOhlcv],
    *,
    provider: str,
    run_id: UUID,
    collected_at: datetime,
) -> list[TechnicalFeature]:
    ordered = sorted(candles, key=lambda candle: candle.trade_date)
    features: list[TechnicalFeature] = []

    for index, candle in enumerate(ordered):
        ma_20 = _simple_ma(ordered, index, 20)
        ma_50 = _simple_ma(ordered, index, 50)
        ma_200 = _simple_ma(ordered, index, 200)
        avg_volume_20 = _average([Decimal(item.volume) for item in ordered[max(0, index - 19) : index + 1]])

        features.append(
            TechnicalFeature(
                trade_date=candle.trade_date,
                provider=provider,
                symbol=candle.symbol,
                close=candle.close,
                volume=candle.volume,
                ma_20=ma_20,
                ma_50=ma_50,
                ma_200=ma_200,
                weekly_ma_50=_weekly_ma(ordered, index, 50),
                weekly_ma_200=_weekly_ma(ordered, index, 200),
                atr_14=_atr(ordered, index, 14),
                return_1d=_return(candle.close, ordered[index - 1].close) if index >= 1 else None,
                return_5d=_return(candle.close, ordered[index - 5].close) if index >= 5 else None,
                return_20d=_return(candle.close, ordered[index - 20].close) if index >= 20 else None,
                volume_ratio_20=float(Decimal(candle.volume) / avg_volume_20) if avg_volume_20 else None,
                htf_trend=_htf_trend(candle.close, ma_50, ma_200),
                run_id=run_id,
                collected_at=collected_at,
            )
        )

    return features


def approximate_volume_profile(
    candles: list[DailyOhlcv],
    *,
    provider: str,
    run_id: UUID,
    collected_at: datetime,
    lookback_days: int = 60,
    bin_count: int = 24,
) -> VolumeProfileLevel:
    if not candles:
        raise ValueError("candles must not be empty")
    if bin_count < 1:
        raise ValueError("bin_count must be positive")

    window = sorted(candles, key=lambda candle: candle.trade_date)[-lookback_days:]
    low = min(candle.low for candle in window)
    high = max(candle.high for candle in window)
    width = (high - low) / Decimal(bin_count)
    if width <= 0:
        width = Decimal("1")

    volumes = [0 for _ in range(bin_count)]
    prices = [_quantize_price(low + width * Decimal(index) + width / Decimal("2")) for index in range(bin_count)]

    for candle in window:
        typical = candle.typical_price
        raw_index = int((typical - low) / width)
        bucket = min(max(raw_index, 0), bin_count - 1)
        volumes[bucket] += candle.volume

    poc_index = max(range(bin_count), key=lambda index: volumes[index])
    poc_price = prices[poc_index]

    target_volume = sum(volumes) * Decimal("0.70")
    selected = {poc_index}
    cumulative = Decimal(volumes[poc_index])
    left = poc_index - 1
    right = poc_index + 1
    while cumulative < target_volume and (left >= 0 or right < bin_count):
        left_volume = volumes[left] if left >= 0 else -1
        right_volume = volumes[right] if right < bin_count else -1
        if right_volume > left_volume:
            selected.add(right)
            cumulative += Decimal(right_volume)
            right += 1
        else:
            selected.add(left)
            cumulative += Decimal(left_volume)
            left -= 1

    bins = [
        {"price": str(prices[index]), "volume": volumes[index]}
        for index in range(bin_count)
        if prices[index] is not None
    ]

    return VolumeProfileLevel(
        trade_date=window[-1].trade_date,
        provider=provider,
        symbol=window[-1].symbol,
        lookback_days=lookback_days,
        source="daily_ohlcv_approximation",
        poc_price=poc_price,
        value_area_low=prices[min(selected)],
        value_area_high=prices[max(selected)],
        bins_json=json.dumps(bins, ensure_ascii=False),
        run_id=run_id,
        collected_at=collected_at,
    )


def _local_lows(candles: list[DailyOhlcv]) -> list[int]:
    return [
        index
        for index in range(1, len(candles) - 1)
        if candles[index].low <= candles[index - 1].low and candles[index].low <= candles[index + 1].low
    ]


def _local_highs(candles: list[DailyOhlcv]) -> list[int]:
    return [
        index
        for index in range(1, len(candles) - 1)
        if candles[index].high >= candles[index - 1].high and candles[index].high >= candles[index + 1].high
    ]


def _is_near(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    if b == 0:
        return a == b
    return abs((a / b) - Decimal("1")) <= tolerance


def _make_event(
    *,
    candle: DailyOhlcv,
    provider: str,
    pattern_name: str,
    pattern_category: str,
    support_price: Decimal | None,
    resistance_price: Decimal | None,
    neckline_price: Decimal | None,
    stop_price: Decimal | None,
    target_price: Decimal | None,
    confidence_score: float,
    features: dict,
    run_id: UUID,
    collected_at: datetime,
) -> PatternEvent:
    return PatternEvent(
        trade_date=candle.trade_date,
        provider=provider,
        symbol=candle.symbol,
        pattern_name=pattern_name,
        pattern_category=pattern_category,
        direction="bullish",
        detection_date=candle.trade_date,
        breakout_date=candle.trade_date,
        retest_date=None,
        neckline_price=neckline_price,
        support_price=support_price,
        resistance_price=resistance_price,
        stop_price=stop_price,
        target_price=target_price,
        confidence_score=confidence_score,
        feature_json=json.dumps(features, ensure_ascii=False, sort_keys=True),
        run_id=run_id,
        collected_at=collected_at,
    )


def _detect_double_bottom(
    candles: list[DailyOhlcv], provider: str, run_id: UUID, collected_at: datetime
) -> PatternEvent | None:
    lows = _local_lows(candles)
    if len(lows) < 2:
        return None

    first, second = lows[-2], lows[-1]
    first_low = candles[first].low
    second_low = candles[second].low
    if not _is_near(first_low, second_low, Decimal("0.04")):
        return None

    neckline = max(candle.high for candle in candles[first : second + 1])
    last = candles[-1]
    if last.close <= neckline:
        return None

    support = min(first_low, second_low)
    measured_move = neckline - support
    return _make_event(
        candle=last,
        provider=provider,
        pattern_name="double_bottom",
        pattern_category="reversal",
        support_price=_quantize_price(support),
        resistance_price=_quantize_price(neckline),
        neckline_price=_quantize_price(neckline),
        stop_price=_quantize_price(support * Decimal("0.99")),
        target_price=_quantize_price(neckline + measured_move),
        confidence_score=0.65,
        features={"low_indexes": [first, second], "tolerance": "0.04"},
        run_id=run_id,
        collected_at=collected_at,
    )


def _detect_inverse_head_and_shoulders(
    candles: list[DailyOhlcv], provider: str, run_id: UUID, collected_at: datetime
) -> PatternEvent | None:
    lows = _local_lows(candles)
    if len(lows) < 3:
        return None

    left, head, right = lows[-3], lows[-2], lows[-1]
    left_low = candles[left].low
    head_low = candles[head].low
    right_low = candles[right].low
    if not (head_low < left_low and head_low < right_low and right_low > head_low):
        return None
    if not _is_near(left_low, right_low, Decimal("0.10")):
        return None

    neckline = max(candle.high for candle in candles[left : right + 1])
    last = candles[-1]
    if last.close <= neckline:
        return None

    measured_move = neckline - head_low
    return _make_event(
        candle=last,
        provider=provider,
        pattern_name="inverse_head_and_shoulders",
        pattern_category="reversal",
        support_price=_quantize_price(head_low),
        resistance_price=_quantize_price(neckline),
        neckline_price=_quantize_price(neckline),
        stop_price=_quantize_price(right_low * Decimal("0.99")),
        target_price=_quantize_price(neckline + measured_move),
        confidence_score=0.68,
        features={"low_indexes": [left, head, right], "right_shoulder_above_head": True},
        run_id=run_id,
        collected_at=collected_at,
    )


def _detect_ascending_triangle(
    candles: list[DailyOhlcv], provider: str, run_id: UUID, collected_at: datetime
) -> PatternEvent | None:
    if len(candles) < 8:
        return None

    window = candles[-8:]
    resistance = max(candle.high for candle in window[:-1])
    resistance_touches = sum(1 for candle in window[:-1] if _is_near(candle.high, resistance, Decimal("0.025")))
    lows = [candle.low for candle in window[:-1]]
    rising_lows = lows[-1] > lows[0] and lows[-2] > lows[1]
    last = window[-1]
    if resistance_touches < 2 or not rising_lows or last.close <= resistance:
        return None

    support = min(lows)
    return _make_event(
        candle=last,
        provider=provider,
        pattern_name="ascending_triangle",
        pattern_category="continuation_or_reversal",
        support_price=_quantize_price(support),
        resistance_price=_quantize_price(resistance),
        neckline_price=None,
        stop_price=_quantize_price(lows[-1] * Decimal("0.99")),
        target_price=_quantize_price(resistance + (resistance - support)),
        confidence_score=0.62,
        features={"resistance_touches": resistance_touches, "rising_lows": rising_lows},
        run_id=run_id,
        collected_at=collected_at,
    )


def _detect_high_tight_flag(
    candles: list[DailyOhlcv], provider: str, run_id: UUID, collected_at: datetime
) -> PatternEvent | None:
    if len(candles) < 20:
        return None

    window = candles[-60:] if len(candles) >= 60 else candles
    low_candle = min(window[:-5], key=lambda candle: candle.low)
    high_candle = max(window[window.index(low_candle) + 1 :], key=lambda candle: candle.high)
    if high_candle.high < low_candle.low * Decimal("1.90"):
        return None

    flag_window = window[window.index(high_candle) :]
    pullback_low = min(candle.low for candle in flag_window)
    pullback = Decimal("1") - (pullback_low / high_candle.high)
    last = window[-1]
    if not (Decimal("0.10") <= pullback <= Decimal("0.25")):
        return None
    if last.close <= max(candle.high for candle in flag_window[:-1]):
        return None

    return _make_event(
        candle=last,
        provider=provider,
        pattern_name="high_tight_flag",
        pattern_category="continuation",
        support_price=_quantize_price(pullback_low),
        resistance_price=_quantize_price(high_candle.high),
        neckline_price=None,
        stop_price=_quantize_price(pullback_low * Decimal("0.99")),
        target_price=_quantize_price(last.close + (high_candle.high - low_candle.low)),
        confidence_score=0.66,
        features={"advance_ratio": str(high_candle.high / low_candle.low), "pullback": str(pullback)},
        run_id=run_id,
        collected_at=collected_at,
    )


def detect_pattern_events(
    candles: list[DailyOhlcv],
    *,
    provider: str,
    run_id: UUID,
    collected_at: datetime,
) -> list[PatternEvent]:
    ordered = sorted(candles, key=lambda candle: candle.trade_date)
    if len(ordered) < 3:
        return []

    detectors = (
        _detect_double_bottom,
        _detect_inverse_head_and_shoulders,
        _detect_ascending_triangle,
        _detect_high_tight_flag,
    )
    events: list[PatternEvent] = []
    for detector in detectors:
        event = detector(ordered, provider, run_id, collected_at)
        if event is not None:
            events.append(event)
    return events


def label_pattern_outcome(
    event: PatternEvent,
    future_candles: list[DailyOhlcv],
    *,
    horizon_days: int,
    run_id: UUID,
    collected_at: datetime,
) -> PatternOutcome:
    if horizon_days < 1:
        raise ValueError("horizon_days must be positive")
    if not future_candles:
        raise ValueError("future_candles must not be empty")

    window = sorted(future_candles, key=lambda candle: candle.trade_date)[:horizon_days]
    entry_price = event.neckline_price or event.resistance_price or window[0].open
    max_high = max(candle.high for candle in window)
    min_low = min(candle.low for candle in window)
    close_at_horizon = window[-1].close if len(window) == horizon_days else None
    target_hit = int(event.target_price is not None and max_high >= event.target_price)
    stop_hit = int(event.stop_price is not None and min_low <= event.stop_price)
    return_at_horizon = _return(close_at_horizon, entry_price) if close_at_horizon is not None else None

    return PatternOutcome(
        detection_date=event.detection_date,
        provider=event.provider,
        symbol=event.symbol,
        pattern_name=event.pattern_name,
        horizon_days=horizon_days,
        entry_price=entry_price,
        stop_price=event.stop_price,
        target_price=event.target_price,
        max_high=max_high,
        min_low=min_low,
        close_at_horizon=close_at_horizon,
        target_hit=target_hit,
        stop_hit=stop_hit,
        return_at_horizon=return_at_horizon,
        run_id=run_id,
        collected_at=collected_at,
    )
