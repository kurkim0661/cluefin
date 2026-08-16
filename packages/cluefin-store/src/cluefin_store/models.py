from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

PRICE_QUANT = Decimal("0.0001")


def _quantize_price(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(PRICE_QUANT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class InsertableRecord:
    def as_insert_row(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DailyUniverseMember(InsertableRecord):
    trade_date: date
    provider: str
    universe_name: str
    universe_type: str
    universe_params_json: str
    market_country: str
    rank: int | None
    symbol: str
    name: str
    selection_metric_name: str | None
    selection_metric_value: Decimal | None
    run_id: UUID
    collected_at: datetime


@dataclass(frozen=True, slots=True)
class DailyOhlcv(InsertableRecord):
    trade_date: date
    provider: str
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    trading_amount: Decimal
    run_id: UUID
    collected_at: datetime

    @property
    def typical_price(self) -> Decimal:
        return _quantize_price((self.high + self.low + self.close) / Decimal("3"))


@dataclass(frozen=True, slots=True)
class TechnicalFeature(InsertableRecord):
    trade_date: date
    provider: str
    symbol: str
    close: Decimal
    volume: int
    ma_20: Decimal | None
    ma_50: Decimal | None
    ma_200: Decimal | None
    weekly_ma_50: Decimal | None
    weekly_ma_200: Decimal | None
    atr_14: Decimal | None
    return_1d: float | None
    return_5d: float | None
    return_20d: float | None
    volume_ratio_20: float | None
    htf_trend: str
    run_id: UUID
    collected_at: datetime


@dataclass(frozen=True, slots=True)
class VolumeProfileLevel(InsertableRecord):
    trade_date: date
    provider: str
    symbol: str
    lookback_days: int
    source: str
    poc_price: Decimal | None
    value_area_low: Decimal | None
    value_area_high: Decimal | None
    bins_json: str
    run_id: UUID
    collected_at: datetime


@dataclass(frozen=True, slots=True)
class PatternEvent(InsertableRecord):
    trade_date: date
    provider: str
    symbol: str
    pattern_name: str
    pattern_category: str
    direction: str
    detection_date: date
    breakout_date: date | None
    retest_date: date | None
    neckline_price: Decimal | None
    support_price: Decimal | None
    resistance_price: Decimal | None
    stop_price: Decimal | None
    target_price: Decimal | None
    confidence_score: float
    feature_json: str
    run_id: UUID
    collected_at: datetime


@dataclass(frozen=True, slots=True)
class PatternOutcome(InsertableRecord):
    detection_date: date
    provider: str
    symbol: str
    pattern_name: str
    horizon_days: int
    entry_price: Decimal
    stop_price: Decimal | None
    target_price: Decimal | None
    max_high: Decimal
    min_low: Decimal
    close_at_horizon: Decimal | None
    target_hit: int
    stop_hit: int
    return_at_horizon: float | None
    run_id: UUID
    collected_at: datetime
