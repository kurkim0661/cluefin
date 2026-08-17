from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from cluefin_store.db import ClickHouseStore
from cluefin_store.models import DailyOhlcv, PatternEvent, PatternOutcome, TechnicalFeature, VolumeProfileLevel
from cluefin_store.patterns import (
    approximate_volume_profile,
    compute_technical_features,
    detect_pattern_events,
    label_pattern_outcome,
)


@dataclass(frozen=True, slots=True)
class PatternAnalysisConfig:
    target_start: date | None = None
    target_end: date | None = None
    pattern_lookback_days: int = 120
    volume_profile_lookback_days: int = 60
    outcome_horizons: tuple[int, ...] = (5, 10, 20)
    retest_window_days: int = 10
    retest_tolerance_pct: Decimal = Decimal("0.03")
    volume_profile_tolerance_pct: Decimal = Decimal("0.03")
    warmup_calendar_days: int = 420


@dataclass(frozen=True, slots=True)
class PatternAnalysisDataset:
    daily_ohlcv: list[DailyOhlcv]
    technical_features: list[TechnicalFeature]
    volume_profile_levels: list[VolumeProfileLevel]
    pattern_events: list[PatternEvent]
    pattern_outcomes: list[PatternOutcome]


class DailyOhlcvProvider(Protocol):
    provider_name: str

    def fetch_daily_ohlcv(
        self, symbols: tuple[str, ...], start_date: date, end_date: date
    ) -> dict[str, list[DailyOhlcv]]:
        pass


PATTERN_ANALYSIS_TABLES: dict[str, str] = {
    "daily_ohlcv": "market.daily_ohlcv",
    "technical_features": "market.daily_technical_features",
    "volume_profile_levels": "market.daily_volume_profile_levels",
    "pattern_events": "market.daily_pattern_events",
    "pattern_outcomes": "market.daily_pattern_outcomes",
}


def required_collection_window(
    *,
    target_start: date,
    target_end: date,
    config: PatternAnalysisConfig | None = None,
) -> tuple[date, date]:
    resolved = config or PatternAnalysisConfig()
    max_horizon = max(resolved.outcome_horizons, default=0)
    return target_start - timedelta(days=resolved.warmup_calendar_days), target_end + timedelta(days=max_horizon)


def build_pattern_analysis_dataset(
    candles_by_symbol: dict[str, list[DailyOhlcv]],
    *,
    provider: str,
    run_id: UUID,
    collected_at: datetime,
    config: PatternAnalysisConfig | None = None,
) -> PatternAnalysisDataset:
    resolved = config or PatternAnalysisConfig()
    all_ohlcv: list[DailyOhlcv] = []
    all_features: list[TechnicalFeature] = []
    all_profiles: list[VolumeProfileLevel] = []
    all_events: list[PatternEvent] = []
    all_outcomes: list[PatternOutcome] = []

    for _symbol, symbol_candles in sorted(candles_by_symbol.items()):
        ordered = sorted(symbol_candles, key=lambda candle: candle.trade_date)
        if not ordered:
            continue

        all_ohlcv.extend(ordered)
        features = compute_technical_features(ordered, provider=provider, run_id=run_id, collected_at=collected_at)
        feature_by_date = {feature.trade_date: feature for feature in features}
        all_features.extend(features)

        profiles_by_date = _compute_profiles_by_date(
            ordered,
            provider=provider,
            run_id=run_id,
            collected_at=collected_at,
            config=resolved,
        )
        all_profiles.extend(profiles_by_date.values())

        for index in range(len(ordered)):
            candle = ordered[index]
            if not _inside_target_window(candle.trade_date, resolved):
                continue

            history = ordered[max(0, index - resolved.pattern_lookback_days + 1) : index + 1]
            events = detect_pattern_events(history, provider=provider, run_id=run_id, collected_at=collected_at)
            for event in events:
                enriched = _enrich_event(
                    event,
                    history=history,
                    feature=feature_by_date.get(event.detection_date),
                    profile=profiles_by_date.get(event.detection_date),
                    future_candles=ordered[index + 1 :],
                    config=resolved,
                )
                all_events.append(enriched)
                for horizon_days in resolved.outcome_horizons:
                    if len(ordered[index + 1 :]) < horizon_days:
                        continue
                    all_outcomes.append(
                        label_pattern_outcome(
                            enriched,
                            ordered[index + 1 :],
                            horizon_days=horizon_days,
                            run_id=run_id,
                            collected_at=collected_at,
                        )
                    )

    return PatternAnalysisDataset(
        daily_ohlcv=all_ohlcv,
        technical_features=all_features,
        volume_profile_levels=all_profiles,
        pattern_events=all_events,
        pattern_outcomes=all_outcomes,
    )


def dataset_table_records(dataset: PatternAnalysisDataset) -> dict[str, list]:
    return {
        PATTERN_ANALYSIS_TABLES["daily_ohlcv"]: dataset.daily_ohlcv,
        PATTERN_ANALYSIS_TABLES["technical_features"]: dataset.technical_features,
        PATTERN_ANALYSIS_TABLES["volume_profile_levels"]: dataset.volume_profile_levels,
        PATTERN_ANALYSIS_TABLES["pattern_events"]: dataset.pattern_events,
        PATTERN_ANALYSIS_TABLES["pattern_outcomes"]: dataset.pattern_outcomes,
    }


def pattern_collection_plan(
    *,
    symbols: tuple[str, ...],
    target_start: date,
    target_end: date,
    config: PatternAnalysisConfig | None = None,
) -> dict:
    resolved = config or PatternAnalysisConfig()
    collection_start, collection_end = required_collection_window(
        target_start=target_start,
        target_end=target_end,
        config=resolved,
    )
    return {
        "required_data": "daily_ohlcv",
        "reason": "pattern detection, HTF moving averages, volume profile approximation, retest and outcome labels",
        "symbols": list(symbols),
        "target_start": target_start.isoformat(),
        "target_end": target_end.isoformat(),
        "collection_start": collection_start.isoformat(),
        "collection_end": collection_end.isoformat(),
        "tables": list(PATTERN_ANALYSIS_TABLES.values()),
        "outcome_horizons": list(resolved.outcome_horizons),
        "pattern_lookback_days": resolved.pattern_lookback_days,
        "volume_profile_lookback_days": resolved.volume_profile_lookback_days,
    }


def collect_and_store_pattern_analysis(
    *,
    provider: DailyOhlcvProvider,
    store: ClickHouseStore,
    symbols: tuple[str, ...],
    target_start: date,
    target_end: date,
    run_id: UUID,
    collected_at: datetime,
    config: PatternAnalysisConfig | None = None,
) -> dict[str, int]:
    resolved = config or PatternAnalysisConfig()
    collection_start, collection_end = required_collection_window(
        target_start=target_start,
        target_end=target_end,
        config=resolved,
    )
    candles_by_symbol = provider.fetch_daily_ohlcv(symbols, collection_start, collection_end)
    dataset = build_pattern_analysis_dataset(
        candles_by_symbol,
        provider=provider.provider_name,
        run_id=run_id,
        collected_at=collected_at,
        config=replace(resolved, target_start=target_start, target_end=target_end),
    )

    summary: dict[str, int] = {}
    for table, records in dataset_table_records(dataset).items():
        summary[table] = store.insert_records(table, records)
    return summary


def _inside_target_window(trade_date: date, config: PatternAnalysisConfig) -> bool:
    if config.target_start is not None and trade_date < config.target_start:
        return False
    if config.target_end is not None and trade_date > config.target_end:
        return False
    return True


def _compute_profiles_by_date(
    candles: list[DailyOhlcv],
    *,
    provider: str,
    run_id: UUID,
    collected_at: datetime,
    config: PatternAnalysisConfig,
) -> dict[date, VolumeProfileLevel]:
    profiles: dict[date, VolumeProfileLevel] = {}
    for index, candle in enumerate(candles):
        if not _inside_target_window(candle.trade_date, config):
            continue
        window = candles[max(0, index - config.volume_profile_lookback_days + 1) : index + 1]
        profiles[candle.trade_date] = approximate_volume_profile(
            window,
            provider=provider,
            run_id=run_id,
            collected_at=collected_at,
            lookback_days=config.volume_profile_lookback_days,
        )
    return profiles


def _enrich_event(
    event: PatternEvent,
    *,
    history: list[DailyOhlcv],
    feature: TechnicalFeature | None,
    profile: VolumeProfileLevel | None,
    future_candles: list[DailyOhlcv],
    config: PatternAnalysisConfig,
) -> PatternEvent:
    htf_trend = feature.htf_trend if feature is not None else "unknown"
    prior_trend = _prior_trend(history)
    pattern_context = _pattern_context(event, prior_trend)
    volume_ratio = feature.volume_ratio_20 if feature and feature.volume_ratio_20 is not None else None
    volume_confirmation = _has_breakout_volume_confirmation(volume_ratio)
    retest_date = _find_retest_date(event, future_candles, config)
    retest_confirmed = int(retest_date is not None)
    volume_profile_confluence = int(_has_volume_profile_confluence(event, profile, config))
    score_components = {
        "shape": event.confidence_score,
        "trend_context": _trend_context_score(pattern_context),
        "htf_alignment": 0.08 if htf_trend == event.direction else 0.0,
        "volume_confirmation": _volume_confirmation_score(volume_ratio),
        "volume_profile": 0.08 if volume_profile_confluence else 0.0,
        "retest": 0.12 if retest_confirmed else 0.0,
    }
    confluence_score = min(1.0, sum(score_components.values()))
    feature_json = _merge_feature_json(
        event.feature_json,
        {
            "direction": event.direction,
            "prior_trend": prior_trend,
            "pattern_context": pattern_context,
            "htf_trend": htf_trend,
            "breakout_volume_ratio": f"{volume_ratio:.4f}" if volume_ratio is not None else None,
            "volume_confirmation": volume_confirmation,
            "volume_profile_confluence": bool(volume_profile_confluence),
            "retest_confirmed": bool(retest_confirmed),
            "confluence_score": confluence_score,
            "score_components": score_components,
            "vwap": str(feature.vwap) if feature and feature.vwap is not None else None,
            "ema_20": str(feature.ema_20) if feature and feature.ema_20 is not None else None,
            "ema_50": str(feature.ema_50) if feature and feature.ema_50 is not None else None,
            "ema_200": str(feature.ema_200) if feature and feature.ema_200 is not None else None,
            "rsi_14": feature.rsi_14 if feature is not None else None,
            "rsi_divergence": feature.rsi_divergence if feature is not None else "none",
            "poc_price": str(profile.poc_price) if profile and profile.poc_price is not None else None,
            "value_area_low": str(profile.value_area_low) if profile and profile.value_area_low is not None else None,
            "value_area_high": str(profile.value_area_high)
            if profile and profile.value_area_high is not None
            else None,
        },
    )
    return replace(
        event,
        retest_date=retest_date,
        feature_json=feature_json,
        htf_trend=htf_trend,
        volume_profile_confluence=volume_profile_confluence,
        retest_confirmed=retest_confirmed,
        confluence_score=confluence_score,
        poc_price=profile.poc_price if profile is not None else None,
        value_area_low=profile.value_area_low if profile is not None else None,
        value_area_high=profile.value_area_high if profile is not None else None,
    )


def _find_retest_date(
    event: PatternEvent, future_candles: list[DailyOhlcv], config: PatternAnalysisConfig
) -> date | None:
    level = event.neckline_price or (event.support_price if event.direction == "bearish" else event.resistance_price)
    if level is None:
        return None
    tolerance = config.retest_tolerance_pct
    for candle in future_candles[: config.retest_window_days]:
        lower_bound = level * (Decimal("1") - tolerance)
        upper_bound = level * (Decimal("1") + tolerance)
        if event.direction == "bearish":
            if candle.high >= lower_bound and candle.close <= upper_bound:
                return candle.trade_date
            continue
        if candle.low <= upper_bound and candle.close >= lower_bound:
            return candle.trade_date
    return None


def _prior_trend(history: list[DailyOhlcv]) -> str:
    prior = sorted(history, key=lambda candle: candle.trade_date)[:-1]
    if len(prior) < 5:
        return "unknown"
    lookback = prior[-20:] if len(prior) > 20 else prior
    start = lookback[0].close
    end = lookback[-1].close
    if start == 0:
        return "unknown"
    change = (end / start) - Decimal("1")
    if change >= Decimal("0.03"):
        return "bullish"
    if change <= Decimal("-0.03"):
        return "bearish"
    return "sideways"


def _pattern_context(event: PatternEvent, prior_trend: str) -> str:
    if prior_trend == "unknown":
        return "unknown"
    if event.pattern_name in {"double_bottom", "inverse_head_and_shoulders"}:
        return "aligned_reversal" if prior_trend == _opposite_direction(event.direction) else "weak_context"
    if event.pattern_name == "high_tight_flag":
        return "aligned_continuation" if prior_trend == event.direction else "weak_context"
    if event.pattern_name in {
        "ascending_triangle",
        "descending_triangle",
        "symmetrical_triangle",
        "rising_wedge",
        "falling_wedge",
    }:
        if prior_trend == event.direction:
            return "aligned_continuation"
        if prior_trend == _opposite_direction(event.direction):
            return "aligned_reversal"
        return "sideways_setup"
    return "unknown"


def _opposite_direction(direction: str) -> str:
    return "bearish" if direction == "bullish" else "bullish"


def _trend_context_score(pattern_context: str) -> float:
    if pattern_context == "aligned_reversal":
        return 0.12
    if pattern_context == "aligned_continuation":
        return 0.09
    if pattern_context == "sideways_setup":
        return 0.03
    return 0.0


def _has_breakout_volume_confirmation(volume_ratio: float | None) -> bool:
    return volume_ratio is not None and volume_ratio >= 1.5


def _volume_confirmation_score(volume_ratio: float | None) -> float:
    if volume_ratio is None:
        return 0.0
    if volume_ratio >= 2.0:
        return 0.15
    if volume_ratio >= 1.5:
        return 0.10
    if volume_ratio >= 1.2:
        return 0.04
    return 0.0


def _has_volume_profile_confluence(
    event: PatternEvent, profile: VolumeProfileLevel | None, config: PatternAnalysisConfig
) -> bool:
    if profile is None:
        return False
    levels = [event.neckline_price, event.resistance_price, event.support_price]
    profile_levels = [profile.poc_price, profile.value_area_low, profile.value_area_high]
    return any(
        _is_near_price(level, profile_level, config.volume_profile_tolerance_pct)
        for level in levels
        for profile_level in profile_levels
    )


def _is_near_price(a: Decimal | None, b: Decimal | None, tolerance: Decimal) -> bool:
    if a is None or b is None:
        return False
    if b == 0:
        return a == b
    return abs((a / b) - Decimal("1")) <= tolerance


def _merge_feature_json(existing: str, extra: dict) -> str:
    try:
        payload = json.loads(existing) if existing else {}
    except json.JSONDecodeError:
        payload = {"raw_feature_json": existing}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
