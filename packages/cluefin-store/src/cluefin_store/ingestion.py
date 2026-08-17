from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from cluefin_store.analysis import PatternAnalysisConfig, build_pattern_analysis_dataset, dataset_table_records
from cluefin_store.db import ClickHouseStore
from cluefin_store.models import DailyOhlcv, DailyUniverseMember


@dataclass(frozen=True, slots=True)
class RankedSymbol:
    rank: int
    symbol: str
    name: str
    selection_metric_value: Decimal | None


class TopRankedProvider(Protocol):
    provider_name: str

    def fetch_ranked_symbols(
        self,
        *,
        ranking_type: str,
        market_country: str,
        duration: str,
        count: int,
    ) -> list[RankedSymbol]:
        pass

    def fetch_daily_ohlcv(
        self, symbols: tuple[str, ...], start_date: date, end_date: date
    ) -> dict[str, list[DailyOhlcv]]:
        pass


@dataclass(frozen=True, slots=True)
class TopBackfillConfig:
    end_date: date
    years: int = 1
    count: int = 50
    ranking_type: str = "MARKET_TRADING_AMOUNT"
    ranking_duration: str = "1y"
    market_country: str = "KR"
    universe_name: str | None = None
    warmup_calendar_days: int = 420
    outcome_horizons: tuple[int, ...] = (5, 10, 20)

    @property
    def target_start(self) -> date:
        return self.end_date - timedelta(days=365 * self.years)

    @property
    def resolved_universe_name(self) -> str:
        if self.universe_name:
            return self.universe_name
        country = self.market_country.lower()
        metric = self.ranking_type.lower()
        return f"{country}_{metric}_top{self.count}_{self.ranking_duration}"


def backfill_top_ranked(
    *,
    provider: TopRankedProvider,
    store: ClickHouseStore,
    config: TopBackfillConfig,
    run_id: UUID,
    collected_at: datetime,
) -> dict[str, int]:
    ranked_symbols = provider.fetch_ranked_symbols(
        ranking_type=config.ranking_type,
        market_country=config.market_country,
        duration=config.ranking_duration,
        count=config.count,
    )
    symbols = tuple(item.symbol for item in ranked_symbols)
    analysis_config = PatternAnalysisConfig(
        target_start=config.target_start,
        target_end=config.end_date,
        warmup_calendar_days=config.warmup_calendar_days,
        outcome_horizons=config.outcome_horizons,
    )
    collection_start = config.target_start - timedelta(days=config.warmup_calendar_days)
    collection_end = config.end_date + timedelta(days=max(config.outcome_horizons, default=0))
    candles_by_symbol = {
        symbol: [
            replace(candle, provider=provider.provider_name, run_id=run_id, collected_at=collected_at)
            for candle in candles
        ]
        for symbol, candles in provider.fetch_daily_ohlcv(symbols, collection_start, collection_end).items()
    }
    dataset = build_pattern_analysis_dataset(
        candles_by_symbol,
        provider=provider.provider_name,
        run_id=run_id,
        collected_at=collected_at,
        config=analysis_config,
    )

    summary: dict[str, int] = {}
    members = _build_universe_members(
        provider=provider.provider_name,
        ranked_symbols=ranked_symbols,
        config=config,
        run_id=run_id,
        collected_at=collected_at,
    )
    summary["market.daily_universe_members"] = store.insert_records("market.daily_universe_members", members)
    for table, records in dataset_table_records(dataset).items():
        summary[table] = store.insert_records(table, records)
    return summary


def _build_universe_members(
    *,
    provider: str,
    ranked_symbols: list[RankedSymbol],
    config: TopBackfillConfig,
    run_id: UUID,
    collected_at: datetime,
) -> list[DailyUniverseMember]:
    params_json = json.dumps(
        {
            "ranking_type": config.ranking_type,
            "ranking_duration": config.ranking_duration,
            "count": config.count,
            "years": config.years,
            "end_date": config.end_date.isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return [
        DailyUniverseMember(
            trade_date=config.end_date,
            provider=provider,
            universe_name=config.resolved_universe_name,
            universe_type="ranking",
            universe_params_json=params_json,
            market_country=config.market_country,
            rank=item.rank,
            symbol=item.symbol,
            name=item.name,
            selection_metric_name=config.ranking_type.lower(),
            selection_metric_value=item.selection_metric_value,
            run_id=run_id,
            collected_at=collected_at,
        )
        for item in ranked_symbols
    ]
