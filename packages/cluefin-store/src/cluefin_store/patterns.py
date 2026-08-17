from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
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


@dataclass(frozen=True, slots=True)
class PatternCandidate:
    trade_date: date
    symbol: str
    pattern_name: str
    pattern_category: str
    direction: str
    completion_score: float
    trigger_price: Decimal | None
    invalidation_price: Decimal | None
    feature_json: str

    def as_chart_dict(self) -> dict:
        return {
            "trade_date": self.trade_date.isoformat(),
            "symbol": self.symbol,
            "pattern_name": self.pattern_name,
            "pattern_category": self.pattern_category,
            "direction": self.direction,
            "completion_score": self.completion_score,
            "trigger_price": self.trigger_price,
            "invalidation_price": self.invalidation_price,
            "features": json.loads(self.feature_json),
        }


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


def _vwap(candle: DailyOhlcv) -> Decimal | None:
    if candle.volume == 0:
        return None
    return _quantize_price(candle.trading_amount / Decimal(candle.volume))


def _ema_values(candles: list[DailyOhlcv], period: int) -> list[Decimal | None]:
    if not candles:
        return []

    multiplier = Decimal("2") / Decimal(period + 1)
    values: list[Decimal | None] = []
    previous = candles[0].close
    for index, candle in enumerate(candles):
        if index == 0:
            previous = candle.close
        else:
            previous = candle.close * multiplier + previous * (Decimal("1") - multiplier)
        values.append(_quantize_price(previous))
    return values


def _rsi_values(candles: list[DailyOhlcv], period: int = 14) -> list[float | None]:
    if len(candles) <= period:
        return [None for _ in candles]

    values: list[float | None] = [None for _ in candles]
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for index in range(1, period + 1):
        change = candles[index].close - candles[index - 1].close
        gains.append(max(change, Decimal("0")))
        losses.append(max(-change, Decimal("0")))

    avg_gain = sum(gains) / Decimal(period)
    avg_loss = sum(losses) / Decimal(period)
    values[period] = _rsi_from_average_gain_loss(avg_gain, avg_loss)

    for index in range(period + 1, len(candles)):
        change = candles[index].close - candles[index - 1].close
        gain = max(change, Decimal("0"))
        loss = max(-change, Decimal("0"))
        avg_gain = ((avg_gain * Decimal(period - 1)) + gain) / Decimal(period)
        avg_loss = ((avg_loss * Decimal(period - 1)) + loss) / Decimal(period)
        values[index] = _rsi_from_average_gain_loss(avg_gain, avg_loss)

    return values


def _rsi_from_average_gain_loss(avg_gain: Decimal, avg_loss: Decimal) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    relative_strength = avg_gain / avg_loss
    return round(float(Decimal("100") - (Decimal("100") / (Decimal("1") + relative_strength))), 4)


def _rsi_divergence_labels(candles: list[DailyOhlcv], rsi_values: list[float | None]) -> list[str]:
    labels = ["none" for _ in candles]
    for index in range(len(candles)):
        confirmed_window = candles[: index + 1]
        lows = [pivot for pivot in _local_lows(confirmed_window) if rsi_values[pivot] is not None]
        highs = [pivot for pivot in _local_highs(confirmed_window) if rsi_values[pivot] is not None]

        if len(lows) >= 2:
            first, second = lows[-2], lows[-1]
            if (
                index - second <= 5
                and candles[second].low < candles[first].low
                and rsi_values[second] > rsi_values[first]
            ):
                labels[index] = "bullish"
                continue

        if len(highs) >= 2:
            first, second = highs[-2], highs[-1]
            if (
                index - second <= 5
                and candles[second].high > candles[first].high
                and rsi_values[second] < rsi_values[first]
            ):
                labels[index] = "bearish"

    return labels


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
    ema_20_values = _ema_values(ordered, 20)
    ema_50_values = _ema_values(ordered, 50)
    ema_200_values = _ema_values(ordered, 200)
    rsi_14_values = _rsi_values(ordered, 14)
    rsi_divergence_labels = _rsi_divergence_labels(ordered, rsi_14_values)

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
                vwap=_vwap(candle),
                ma_20=ma_20,
                ma_50=ma_50,
                ma_200=ma_200,
                ema_20=ema_20_values[index],
                ema_50=ema_50_values[index],
                ema_200=ema_200_values[index],
                weekly_ma_50=_weekly_ma(ordered, index, 50),
                weekly_ma_200=_weekly_ma(ordered, index, 200),
                atr_14=_atr(ordered, index, 14),
                rsi_14=rsi_14_values[index],
                rsi_divergence=rsi_divergence_labels[index],
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


def _is_fresh_breakout(candles: list[DailyOhlcv], level: Decimal) -> bool:
    if len(candles) < 2:
        return False
    return candles[-1].close > level and candles[-2].close <= level


def _is_fresh_breakdown(candles: list[DailyOhlcv], level: Decimal) -> bool:
    if len(candles) < 2:
        return False
    return candles[-1].close < level and candles[-2].close >= level


def _geometry_point(role: str, candle: DailyOhlcv, price: Decimal) -> dict[str, str]:
    return {
        "role": role,
        "date": candle.trade_date.isoformat(),
        "price": str(_quantize_price(price)),
    }


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
    direction: str = "bullish",
) -> PatternEvent:
    return PatternEvent(
        trade_date=candle.trade_date,
        provider=provider,
        symbol=candle.symbol,
        pattern_name=pattern_name,
        pattern_category=pattern_category,
        direction=direction,
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


def _make_candidate(
    *,
    candle: DailyOhlcv,
    pattern_name: str,
    pattern_category: str,
    direction: str,
    completion_score: float,
    trigger_price: Decimal | None,
    invalidation_price: Decimal | None,
    features: dict,
) -> PatternCandidate:
    return PatternCandidate(
        trade_date=candle.trade_date,
        symbol=candle.symbol,
        pattern_name=pattern_name,
        pattern_category=pattern_category,
        direction=direction,
        completion_score=round(max(0.0, min(completion_score, 0.99)), 4),
        trigger_price=_quantize_price(trigger_price),
        invalidation_price=_quantize_price(invalidation_price),
        feature_json=json.dumps(features, ensure_ascii=False, sort_keys=True),
    )


def _completion_between(value: Decimal, invalidation: Decimal, trigger: Decimal) -> float:
    if trigger == invalidation:
        return 0.0
    progress = (value - invalidation) / (trigger - invalidation)
    return float(max(Decimal("0"), min(progress, Decimal("0.99"))))


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
    if not _is_fresh_breakout(candles, neckline):
        return None

    last = candles[-1]
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
        features={
            "low_indexes": [first, second],
            "tolerance": "0.04",
            "geometry": {
                "kind": "double_bottom",
                "points": [
                    _geometry_point("first_bottom", candles[first], first_low),
                    _geometry_point("second_bottom", candles[second], second_low),
                    _geometry_point("breakout", last, last.close),
                ],
                "levels": {
                    "neckline": str(_quantize_price(neckline)),
                    "support": str(_quantize_price(support)),
                    "target": str(_quantize_price(neckline + measured_move)),
                },
            },
        },
        run_id=run_id,
        collected_at=collected_at,
    )


def _candidate_double_bottom(candles: list[DailyOhlcv]) -> PatternCandidate | None:
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
    support = min(first_low, second_low)
    if last.close >= neckline:
        return None
    completion = _completion_between(last.close, support, neckline)
    if completion < 0.55:
        return None
    measured_move = neckline - support
    return _make_candidate(
        candle=last,
        pattern_name="double_bottom",
        pattern_category="reversal",
        direction="bullish",
        completion_score=completion,
        trigger_price=neckline,
        invalidation_price=support * Decimal("0.99"),
        features={
            "missing_condition": "close_above_neckline",
            "geometry": {
                "kind": "double_bottom",
                "points": [
                    _geometry_point("first_bottom", candles[first], first_low),
                    _geometry_point("second_bottom", candles[second], second_low),
                    _geometry_point("forming", last, last.close),
                ],
                "levels": {
                    "neckline": str(_quantize_price(neckline)),
                    "support": str(_quantize_price(support)),
                    "target": str(_quantize_price(neckline + measured_move)),
                },
            },
        },
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

    reaction_highs = [candle.high for candle in candles[left + 1 : head]] + [
        candle.high for candle in candles[head + 1 : right]
    ]
    if not reaction_highs:
        return None
    neckline = max(reaction_highs)
    if neckline <= max(left_low, right_low):
        return None
    if not _is_fresh_breakout(candles, neckline):
        return None

    last = candles[-1]
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
        features={
            "low_indexes": [left, head, right],
            "right_shoulder_above_head": True,
            "geometry": {
                "kind": "inverse_head_and_shoulders",
                "points": [
                    _geometry_point("left_shoulder", candles[left], left_low),
                    _geometry_point("head", candles[head], head_low),
                    _geometry_point("right_shoulder", candles[right], right_low),
                ],
                "levels": {
                    "neckline": str(_quantize_price(neckline)),
                    "support": str(_quantize_price(head_low)),
                    "target": str(_quantize_price(neckline + measured_move)),
                },
            },
        },
        run_id=run_id,
        collected_at=collected_at,
    )


def _candidate_inverse_head_and_shoulders(candles: list[DailyOhlcv]) -> PatternCandidate | None:
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
    reaction_highs = [candle.high for candle in candles[left + 1 : head]] + [
        candle.high for candle in candles[head + 1 : right]
    ]
    if not reaction_highs:
        return None
    neckline = max(reaction_highs)
    if neckline <= max(left_low, right_low):
        return None
    last = candles[-1]
    if last.close >= neckline:
        return None
    completion = _completion_between(last.close, head_low, neckline)
    if completion < 0.55:
        return None
    measured_move = neckline - head_low
    return _make_candidate(
        candle=last,
        pattern_name="inverse_head_and_shoulders",
        pattern_category="reversal",
        direction="bullish",
        completion_score=completion,
        trigger_price=neckline,
        invalidation_price=head_low,
        features={
            "missing_condition": "close_above_neckline",
            "geometry": {
                "kind": "inverse_head_and_shoulders",
                "points": [
                    _geometry_point("left_shoulder", candles[left], left_low),
                    _geometry_point("head", candles[head], head_low),
                    _geometry_point("right_shoulder", candles[right], right_low),
                    _geometry_point("forming", last, last.close),
                ],
                "levels": {
                    "neckline": str(_quantize_price(neckline)),
                    "support": str(_quantize_price(head_low)),
                    "target": str(_quantize_price(neckline + measured_move)),
                },
            },
        },
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
    if resistance_touches < 2 or not rising_lows or not _is_fresh_breakout(window, resistance):
        return None

    support = min(lows)
    first_resistance = max(window[:-1], key=lambda candle: candle.high)
    last_resistance = max(reversed(window[:-1]), key=lambda candle: candle.high)
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
        features={
            "resistance_touches": resistance_touches,
            "rising_lows": rising_lows,
            "geometry": {
                "kind": "ascending_triangle",
                "points": [
                    _geometry_point("upper_start", first_resistance, resistance),
                    _geometry_point("lower_start", window[0], lows[0]),
                    _geometry_point("upper_end", last_resistance, resistance),
                    _geometry_point("lower_end", window[-2], lows[-1]),
                    _geometry_point("breakout", last, last.close),
                ],
                "segments": [
                    ["upper_start", "upper_end"],
                    ["lower_start", "lower_end"],
                    ["upper_end", "breakout"],
                ],
                "levels": {
                    "resistance": str(_quantize_price(resistance)),
                    "support": str(_quantize_price(support)),
                    "target": str(_quantize_price(resistance + (resistance - support))),
                },
            },
        },
        run_id=run_id,
        collected_at=collected_at,
    )


def _candidate_ascending_triangle(candles: list[DailyOhlcv]) -> PatternCandidate | None:
    if len(candles) < 8:
        return None
    window = candles[-8:]
    resistance = max(candle.high for candle in window[:-1])
    resistance_touches = sum(1 for candle in window[:-1] if _is_near(candle.high, resistance, Decimal("0.025")))
    lows = [candle.low for candle in window[:-1]]
    rising_lows = lows[-1] > lows[0] and lows[-2] > lows[1]
    last = window[-1]
    if resistance_touches < 2 or not rising_lows or last.close >= resistance:
        return None
    support = min(lows)
    completion = _completion_between(last.close, support, resistance)
    if completion < 0.55:
        return None
    first_resistance = max(window[:-1], key=lambda candle: candle.high)
    last_resistance = max(reversed(window[:-1]), key=lambda candle: candle.high)
    return _make_candidate(
        candle=last,
        pattern_name="ascending_triangle",
        pattern_category="continuation_or_reversal",
        direction="bullish",
        completion_score=completion,
        trigger_price=resistance,
        invalidation_price=support,
        features={
            "missing_condition": "close_above_resistance",
            "geometry": {
                "kind": "ascending_triangle",
                "points": [
                    _geometry_point("upper_start", first_resistance, resistance),
                    _geometry_point("lower_start", window[0], lows[0]),
                    _geometry_point("upper_end", last_resistance, resistance),
                    _geometry_point("lower_end", window[-2], lows[-1]),
                    _geometry_point("forming", last, last.close),
                ],
                "segments": [
                    ["upper_start", "upper_end"],
                    ["lower_start", "lower_end"],
                ],
                "levels": {
                    "resistance": str(_quantize_price(resistance)),
                    "support": str(_quantize_price(support)),
                    "target": str(_quantize_price(resistance + (resistance - support))),
                },
            },
        },
    )


def _detect_descending_triangle(
    candles: list[DailyOhlcv], provider: str, run_id: UUID, collected_at: datetime
) -> PatternEvent | None:
    if len(candles) < 8:
        return None

    window = candles[-8:]
    formation = window[:-1]
    support = min(candle.low for candle in formation)
    support_touches = sum(1 for candle in formation if _is_near(candle.low, support, Decimal("0.025")))
    highs = [candle.high for candle in formation]
    falling_highs = highs[-1] < highs[0] and highs[-2] < highs[1]
    last = window[-1]
    if support_touches < 2 or not falling_highs or not _is_fresh_breakdown(window, support):
        return None

    resistance_start = highs[0]
    resistance_end = highs[-1]
    measured_move = resistance_start - support
    target = support - measured_move
    return _make_event(
        candle=last,
        provider=provider,
        pattern_name="descending_triangle",
        pattern_category="bearish_continuation_or_reversal",
        direction="bearish",
        support_price=_quantize_price(support),
        resistance_price=_quantize_price(resistance_end),
        neckline_price=None,
        stop_price=_quantize_price(resistance_end),
        target_price=_quantize_price(target),
        confidence_score=0.62,
        features={
            "support_touches": support_touches,
            "falling_highs": falling_highs,
            "geometry": {
                "kind": "descending_triangle",
                "points": [
                    _geometry_point("upper_start", formation[0], resistance_start),
                    _geometry_point("lower_start", formation[0], support),
                    _geometry_point("upper_end", formation[-1], resistance_end),
                    _geometry_point("lower_end", formation[-1], support),
                    _geometry_point("breakdown", last, last.close),
                ],
                "segments": [
                    ["upper_start", "upper_end"],
                    ["lower_start", "lower_end"],
                    ["lower_end", "breakdown"],
                ],
                "levels": {
                    "support": str(_quantize_price(support)),
                    "resistance": str(_quantize_price(resistance_end)),
                    "target": str(_quantize_price(target)),
                },
            },
        },
        run_id=run_id,
        collected_at=collected_at,
    )


def _candidate_descending_triangle(candles: list[DailyOhlcv]) -> PatternCandidate | None:
    if len(candles) < 8:
        return None
    window = candles[-8:]
    formation = window[:-1]
    support = min(candle.low for candle in formation)
    support_touches = sum(1 for candle in formation if _is_near(candle.low, support, Decimal("0.025")))
    highs = [candle.high for candle in formation]
    falling_highs = highs[-1] < highs[0] and highs[-2] < highs[1]
    last = window[-1]
    if support_touches < 2 or not falling_highs or last.close <= support:
        return None

    resistance_start = highs[0]
    resistance_end = highs[-1]
    completion = _completion_between(last.close, resistance_end, support)
    if completion < 0.55:
        return None
    measured_move = resistance_start - support
    target = support - measured_move
    return _make_candidate(
        candle=last,
        pattern_name="descending_triangle",
        pattern_category="bearish_continuation_or_reversal",
        direction="bearish",
        completion_score=completion,
        trigger_price=support,
        invalidation_price=resistance_end,
        features={
            "missing_condition": "close_below_support",
            "support_touches": support_touches,
            "falling_highs": falling_highs,
            "geometry": {
                "kind": "descending_triangle",
                "points": [
                    _geometry_point("upper_start", formation[0], resistance_start),
                    _geometry_point("lower_start", formation[0], support),
                    _geometry_point("upper_end", formation[-1], resistance_end),
                    _geometry_point("lower_end", formation[-1], support),
                    _geometry_point("forming", last, last.close),
                ],
                "segments": [
                    ["upper_start", "upper_end"],
                    ["lower_start", "lower_end"],
                ],
                "levels": {
                    "support": str(_quantize_price(support)),
                    "resistance": str(_quantize_price(resistance_end)),
                    "target": str(_quantize_price(target)),
                },
            },
        },
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
    if len(flag_window) < 2:
        return None
    flag_resistance = max(candle.high for candle in flag_window[:-1])
    if not _is_fresh_breakout(flag_window, flag_resistance):
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


def _candidate_high_tight_flag(candles: list[DailyOhlcv]) -> PatternCandidate | None:
    if len(candles) < 20:
        return None
    window = candles[-60:] if len(candles) >= 60 else candles
    low_candle = min(window[:-5], key=lambda candle: candle.low)
    high_candle = max(window[window.index(low_candle) + 1 :], key=lambda candle: candle.high)
    if high_candle.high < low_candle.low * Decimal("1.90"):
        return None
    flag_window = window[window.index(high_candle) :]
    if len(flag_window) < 2:
        return None
    pullback_low = min(candle.low for candle in flag_window)
    pullback = Decimal("1") - (pullback_low / high_candle.high)
    if not (Decimal("0.10") <= pullback <= Decimal("0.25")):
        return None
    flag_resistance = max(candle.high for candle in flag_window[:-1])
    last = window[-1]
    if last.close >= flag_resistance:
        return None
    completion = _completion_between(last.close, pullback_low, flag_resistance)
    if completion < 0.55:
        return None
    return _make_candidate(
        candle=last,
        pattern_name="high_tight_flag",
        pattern_category="continuation",
        direction="bullish",
        completion_score=completion,
        trigger_price=flag_resistance,
        invalidation_price=pullback_low,
        features={
            "missing_condition": "close_above_flag_resistance",
            "advance_ratio": str(high_candle.high / low_candle.low),
            "pullback": str(pullback),
            "geometry": {
                "kind": "high_tight_flag",
                "points": [
                    _geometry_point("advance_start", low_candle, low_candle.low),
                    _geometry_point("flag_high", high_candle, high_candle.high),
                    _geometry_point("forming", last, last.close),
                ],
                "levels": {
                    "resistance": str(_quantize_price(flag_resistance)),
                    "support": str(_quantize_price(pullback_low)),
                    "target": str(_quantize_price(flag_resistance + (high_candle.high - low_candle.low))),
                },
            },
        },
    )


def _detect_rising_wedge(
    candles: list[DailyOhlcv], provider: str, run_id: UUID, collected_at: datetime
) -> PatternEvent | None:
    if len(candles) < 10:
        return None

    window = candles[-10:]
    formation = window[:-1]
    lower_start = formation[0].low
    lower_end = formation[-1].low
    upper_start = formation[0].high
    upper_end = formation[-1].high
    start_width = upper_start - lower_start
    end_width = upper_end - lower_end
    if start_width <= 0 or end_width <= 0:
        return None
    if not (lower_end > lower_start and upper_end > upper_start and end_width < start_width):
        return None

    lower_slope = (lower_end - lower_start) / Decimal(len(formation) - 1)
    upper_slope = (upper_end - upper_start) / Decimal(len(formation) - 1)
    if lower_slope <= upper_slope:
        return None
    if not _is_fresh_breakdown(window, lower_end):
        return None

    last = window[-1]
    target = lower_end - end_width
    return _make_event(
        candle=last,
        provider=provider,
        pattern_name="rising_wedge",
        pattern_category="bearish_reversal_or_continuation",
        direction="bearish",
        support_price=_quantize_price(lower_end),
        resistance_price=_quantize_price(upper_end),
        neckline_price=None,
        stop_price=_quantize_price(upper_end),
        target_price=_quantize_price(target),
        confidence_score=0.64,
        features={
            "start_width": str(_quantize_price(start_width)),
            "end_width": str(_quantize_price(end_width)),
            "lower_slope": str(_quantize_price(lower_slope)),
            "upper_slope": str(_quantize_price(upper_slope)),
            "geometry": {
                "kind": "rising_wedge",
                "points": [
                    _geometry_point("lower_start", formation[0], lower_start),
                    _geometry_point("upper_start", formation[0], upper_start),
                    _geometry_point("lower_end", formation[-1], lower_end),
                    _geometry_point("upper_end", formation[-1], upper_end),
                    _geometry_point("breakdown", last, last.close),
                ],
                "segments": [
                    ["lower_start", "lower_end"],
                    ["upper_start", "upper_end"],
                    ["lower_end", "breakdown"],
                ],
                "levels": {
                    "support": str(_quantize_price(lower_end)),
                    "resistance": str(_quantize_price(upper_end)),
                    "target": str(_quantize_price(target)),
                },
            },
        },
        run_id=run_id,
        collected_at=collected_at,
    )


def _candidate_rising_wedge(candles: list[DailyOhlcv]) -> PatternCandidate | None:
    if len(candles) < 10:
        return None
    window = candles[-10:]
    formation = window[:-1]
    lower_start = formation[0].low
    lower_end = formation[-1].low
    upper_start = formation[0].high
    upper_end = formation[-1].high
    start_width = upper_start - lower_start
    end_width = upper_end - lower_end
    if start_width <= 0 or end_width <= 0:
        return None
    if not (lower_end > lower_start and upper_end > upper_start and end_width < start_width):
        return None
    lower_slope = (lower_end - lower_start) / Decimal(len(formation) - 1)
    upper_slope = (upper_end - upper_start) / Decimal(len(formation) - 1)
    if lower_slope <= upper_slope:
        return None
    last = window[-1]
    if last.close <= lower_end:
        return None
    completion = _completion_between(last.close, upper_end, lower_end)
    if completion < 0.55:
        return None
    target = lower_end - end_width
    return _make_candidate(
        candle=last,
        pattern_name="rising_wedge",
        pattern_category="bearish_reversal_or_continuation",
        direction="bearish",
        completion_score=completion,
        trigger_price=lower_end,
        invalidation_price=upper_end,
        features={
            "missing_condition": "close_below_support",
            "geometry": {
                "kind": "rising_wedge",
                "points": [
                    _geometry_point("lower_start", formation[0], lower_start),
                    _geometry_point("upper_start", formation[0], upper_start),
                    _geometry_point("lower_end", formation[-1], lower_end),
                    _geometry_point("upper_end", formation[-1], upper_end),
                    _geometry_point("forming", last, last.close),
                ],
                "segments": [
                    ["lower_start", "lower_end"],
                    ["upper_start", "upper_end"],
                ],
                "levels": {
                    "support": str(_quantize_price(lower_end)),
                    "resistance": str(_quantize_price(upper_end)),
                    "target": str(_quantize_price(target)),
                },
            },
        },
    )


def _detect_falling_wedge(
    candles: list[DailyOhlcv], provider: str, run_id: UUID, collected_at: datetime
) -> PatternEvent | None:
    if len(candles) < 10:
        return None

    window = candles[-10:]
    formation = window[:-1]
    upper_start = formation[0].high
    upper_end = formation[-1].high
    lower_start = formation[0].low
    lower_end = formation[-1].low
    start_width = upper_start - lower_start
    end_width = upper_end - lower_end
    if start_width <= 0 or end_width <= 0:
        return None
    if not (upper_end < upper_start and lower_end < lower_start and end_width < start_width):
        return None

    upper_slope = (upper_end - upper_start) / Decimal(len(formation) - 1)
    lower_slope = (lower_end - lower_start) / Decimal(len(formation) - 1)
    if abs(upper_slope) <= abs(lower_slope):
        return None
    if not _is_fresh_breakout(window, upper_end):
        return None

    last = window[-1]
    target = upper_end + end_width
    return _make_event(
        candle=last,
        provider=provider,
        pattern_name="falling_wedge",
        pattern_category="bullish_reversal_or_continuation",
        support_price=_quantize_price(lower_end),
        resistance_price=_quantize_price(upper_end),
        neckline_price=None,
        stop_price=_quantize_price(lower_end),
        target_price=_quantize_price(target),
        confidence_score=0.64,
        features={
            "start_width": str(_quantize_price(start_width)),
            "end_width": str(_quantize_price(end_width)),
            "upper_slope": str(_quantize_price(upper_slope)),
            "lower_slope": str(_quantize_price(lower_slope)),
            "geometry": {
                "kind": "falling_wedge",
                "points": [
                    _geometry_point("upper_start", formation[0], upper_start),
                    _geometry_point("lower_start", formation[0], lower_start),
                    _geometry_point("upper_end", formation[-1], upper_end),
                    _geometry_point("lower_end", formation[-1], lower_end),
                    _geometry_point("breakout", last, last.close),
                ],
                "segments": [
                    ["upper_start", "upper_end"],
                    ["lower_start", "lower_end"],
                    ["upper_end", "breakout"],
                ],
                "levels": {
                    "support": str(_quantize_price(lower_end)),
                    "resistance": str(_quantize_price(upper_end)),
                    "target": str(_quantize_price(target)),
                },
            },
        },
        run_id=run_id,
        collected_at=collected_at,
    )


def _candidate_falling_wedge(candles: list[DailyOhlcv]) -> PatternCandidate | None:
    if len(candles) < 10:
        return None
    window = candles[-10:]
    formation = window[:-1]
    upper_start = formation[0].high
    upper_end = formation[-1].high
    lower_start = formation[0].low
    lower_end = formation[-1].low
    start_width = upper_start - lower_start
    end_width = upper_end - lower_end
    if start_width <= 0 or end_width <= 0:
        return None
    if not (upper_end < upper_start and lower_end < lower_start and end_width < start_width):
        return None
    upper_slope = (upper_end - upper_start) / Decimal(len(formation) - 1)
    lower_slope = (lower_end - lower_start) / Decimal(len(formation) - 1)
    if abs(upper_slope) <= abs(lower_slope):
        return None
    last = window[-1]
    if last.close >= upper_end:
        return None
    completion = _completion_between(last.close, lower_end, upper_end)
    if completion < 0.55:
        return None
    target = upper_end + end_width
    return _make_candidate(
        candle=last,
        pattern_name="falling_wedge",
        pattern_category="bullish_reversal_or_continuation",
        direction="bullish",
        completion_score=completion,
        trigger_price=upper_end,
        invalidation_price=lower_end,
        features={
            "missing_condition": "close_above_resistance",
            "geometry": {
                "kind": "falling_wedge",
                "points": [
                    _geometry_point("upper_start", formation[0], upper_start),
                    _geometry_point("lower_start", formation[0], lower_start),
                    _geometry_point("upper_end", formation[-1], upper_end),
                    _geometry_point("lower_end", formation[-1], lower_end),
                    _geometry_point("forming", last, last.close),
                ],
                "segments": [
                    ["upper_start", "upper_end"],
                    ["lower_start", "lower_end"],
                ],
                "levels": {
                    "support": str(_quantize_price(lower_end)),
                    "resistance": str(_quantize_price(upper_end)),
                    "target": str(_quantize_price(target)),
                },
            },
        },
    )


def _symmetrical_triangle_bounds(
    candles: list[DailyOhlcv],
) -> tuple[list[DailyOhlcv], Decimal, Decimal, Decimal, Decimal, Decimal, Decimal] | None:
    if len(candles) < 10:
        return None
    window = candles[-10:]
    formation = window[:-1]
    upper_start = formation[0].high
    upper_end = formation[-1].high
    lower_start = formation[0].low
    lower_end = formation[-1].low
    start_width = upper_start - lower_start
    end_width = upper_end - lower_end
    if start_width <= 0 or end_width <= 0:
        return None
    if not (upper_end < upper_start and lower_end > lower_start and end_width < start_width):
        return None
    upper_slope = (upper_end - upper_start) / Decimal(len(formation) - 1)
    lower_slope = (lower_end - lower_start) / Decimal(len(formation) - 1)
    if upper_slope >= 0 or lower_slope <= 0:
        return None
    return formation, upper_start, lower_start, upper_end, lower_end, start_width, end_width


def _detect_symmetrical_triangle(
    candles: list[DailyOhlcv], provider: str, run_id: UUID, collected_at: datetime
) -> PatternEvent | None:
    bounds = _symmetrical_triangle_bounds(candles)
    if bounds is None:
        return None
    formation, upper_start, lower_start, upper_end, lower_end, start_width, end_width = bounds
    window = candles[-10:]
    last = window[-1]
    previous = window[-2]

    if last.close > upper_end and previous.close <= upper_end:
        direction = "bullish"
        trigger = upper_end
        stop = lower_end
        target = upper_end + start_width
        breakout_role = "breakout"
        pattern_category = "bullish_continuation_or_reversal"
    elif last.close < lower_end and previous.close >= lower_end:
        direction = "bearish"
        trigger = lower_end
        stop = upper_end
        target = lower_end - start_width
        breakout_role = "breakdown"
        pattern_category = "bearish_continuation_or_reversal"
    else:
        return None

    return _make_event(
        candle=last,
        provider=provider,
        pattern_name="symmetrical_triangle",
        pattern_category=pattern_category,
        direction=direction,
        support_price=_quantize_price(lower_end),
        resistance_price=_quantize_price(upper_end),
        neckline_price=None,
        stop_price=_quantize_price(stop),
        target_price=_quantize_price(target),
        confidence_score=0.63,
        features={
            "start_width": str(_quantize_price(start_width)),
            "end_width": str(_quantize_price(end_width)),
            "geometry": {
                "kind": "symmetrical_triangle",
                "points": [
                    _geometry_point("upper_start", formation[0], upper_start),
                    _geometry_point("lower_start", formation[0], lower_start),
                    _geometry_point("upper_end", formation[-1], upper_end),
                    _geometry_point("lower_end", formation[-1], lower_end),
                    _geometry_point(breakout_role, last, last.close),
                ],
                "segments": [
                    ["upper_start", "upper_end"],
                    ["lower_start", "lower_end"],
                    ["upper_end" if direction == "bullish" else "lower_end", breakout_role],
                ],
                "levels": {
                    "support": str(_quantize_price(lower_end)),
                    "resistance": str(_quantize_price(upper_end)),
                    "target": str(_quantize_price(target)),
                    "trigger": str(_quantize_price(trigger)),
                },
            },
        },
        run_id=run_id,
        collected_at=collected_at,
    )


def _candidate_symmetrical_triangle(candles: list[DailyOhlcv]) -> PatternCandidate | None:
    bounds = _symmetrical_triangle_bounds(candles)
    if bounds is None:
        return None
    formation, upper_start, lower_start, upper_end, lower_end, start_width, _end_width = bounds
    last = candles[-1]
    if not (lower_end < last.close < upper_end):
        return None

    bullish_completion = _completion_between(last.close, lower_end, upper_end)
    bearish_completion = _completion_between(last.close, upper_end, lower_end)
    if bullish_completion >= bearish_completion:
        direction = "bullish"
        completion = bullish_completion
        trigger = upper_end
        invalidation = lower_end
        missing_condition = "close_above_resistance"
        target = upper_end + start_width
    else:
        direction = "bearish"
        completion = bearish_completion
        trigger = lower_end
        invalidation = upper_end
        missing_condition = "close_below_support"
        target = lower_end - start_width
    if completion < 0.55:
        return None

    return _make_candidate(
        candle=last,
        pattern_name="symmetrical_triangle",
        pattern_category=f"{direction}_continuation_or_reversal",
        direction=direction,
        completion_score=completion,
        trigger_price=trigger,
        invalidation_price=invalidation,
        features={
            "missing_condition": missing_condition,
            "geometry": {
                "kind": "symmetrical_triangle",
                "points": [
                    _geometry_point("upper_start", formation[0], upper_start),
                    _geometry_point("lower_start", formation[0], lower_start),
                    _geometry_point("upper_end", formation[-1], upper_end),
                    _geometry_point("lower_end", formation[-1], lower_end),
                    _geometry_point("forming", last, last.close),
                ],
                "segments": [
                    ["upper_start", "upper_end"],
                    ["lower_start", "lower_end"],
                ],
                "levels": {
                    "support": str(_quantize_price(lower_end)),
                    "resistance": str(_quantize_price(upper_end)),
                    "target": str(_quantize_price(target)),
                    "trigger": str(_quantize_price(trigger)),
                },
            },
        },
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
        _detect_descending_triangle,
        _detect_high_tight_flag,
        _detect_rising_wedge,
        _detect_falling_wedge,
        _detect_symmetrical_triangle,
    )
    events: list[PatternEvent] = []
    for detector in detectors:
        event = detector(ordered, provider, run_id, collected_at)
        if event is not None:
            events.append(event)
    return events


def detect_pattern_candidates(candles: list[DailyOhlcv]) -> list[PatternCandidate]:
    ordered = sorted(candles, key=lambda candle: candle.trade_date)
    if len(ordered) < 3:
        return []

    detectors = (
        _candidate_double_bottom,
        _candidate_inverse_head_and_shoulders,
        _candidate_ascending_triangle,
        _candidate_descending_triangle,
        _candidate_high_tight_flag,
        _candidate_rising_wedge,
        _candidate_falling_wedge,
        _candidate_symmetrical_triangle,
    )
    candidates: list[PatternCandidate] = []
    for detector in detectors:
        candidate = detector(ordered)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


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
    entry_price = _entry_price(event, window[0])
    max_high = max(candle.high for candle in window)
    min_low = min(candle.low for candle in window)
    close_at_horizon = window[-1].close if len(window) == horizon_days else None
    if event.direction == "bearish":
        target_hit = int(event.target_price is not None and min_low <= event.target_price)
        stop_hit = int(event.stop_price is not None and max_high >= event.stop_price)
        return_at_horizon = _return(entry_price, close_at_horizon) if close_at_horizon is not None else None
    else:
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


def _entry_price(event: PatternEvent, fallback: DailyOhlcv) -> Decimal:
    if event.direction == "bearish":
        return event.neckline_price or event.support_price or fallback.open
    return event.neckline_price or event.resistance_price or fallback.open
