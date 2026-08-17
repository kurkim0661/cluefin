from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from cluefin_store.ingestion import RankedSymbol, TopBackfillConfig, backfill_top_ranked
from cluefin_store.models import DailyOhlcv

RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
COLLECTED_AT = datetime(2026, 8, 16, 16, 0, 0)


def make_candle(index: int, close: str, *, symbol: str = "005930") -> DailyOhlcv:
    close_price = Decimal(close)
    return DailyOhlcv(
        trade_date=date(2025, 8, 17) + timedelta(days=index),
        provider="toss",
        symbol=symbol,
        open=close_price - Decimal("1"),
        high=close_price + Decimal("2"),
        low=close_price - Decimal("2"),
        close=close_price,
        volume=1000,
        trading_amount=close_price * Decimal("1000"),
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
    )


class FakeProvider:
    provider_name = "toss"

    def __init__(self) -> None:
        self.ranking_calls: list[tuple[str, str, str, int]] = []
        self.ohlcv_calls: list[tuple[tuple[str, ...], date, date]] = []

    def fetch_ranked_symbols(
        self,
        *,
        ranking_type: str,
        market_country: str,
        duration: str,
        count: int,
    ) -> list[RankedSymbol]:
        self.ranking_calls.append((ranking_type, market_country, duration, count))
        return [
            RankedSymbol(rank=1, symbol="005930", name="삼성전자", selection_metric_value=Decimal("1000000")),
            RankedSymbol(rank=2, symbol="000660", name="SK하이닉스", selection_metric_value=Decimal("900000")),
        ]

    def fetch_daily_ohlcv(
        self, symbols: tuple[str, ...], start_date: date, end_date: date
    ) -> dict[str, list[DailyOhlcv]]:
        self.ohlcv_calls.append((symbols, start_date, end_date))
        return {
            symbol: [make_candle(index, str(100 + index), symbol=symbol) for index in range(30)] for symbol in symbols
        }


class FakeStore:
    def __init__(self) -> None:
        self.inserts: dict[str, int] = {}

    def insert_records(self, table: str, records: list) -> int:
        self.inserts[table] = len(records)
        return len(records)


def test_backfill_top_ranked_inserts_universe_and_analysis_rows() -> None:
    provider = FakeProvider()
    store = FakeStore()
    config = TopBackfillConfig(
        end_date=date(2026, 8, 16),
        years=1,
        count=2,
        ranking_duration="1y",
        warmup_calendar_days=0,
        outcome_horizons=(5,),
    )

    summary = backfill_top_ranked(
        provider=provider,
        store=store,
        config=config,
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
    )

    assert provider.ranking_calls == [("MARKET_TRADING_AMOUNT", "KR", "1y", 2)]
    assert provider.ohlcv_calls == [(("005930", "000660"), date(2025, 8, 16), date(2026, 8, 21))]
    assert summary["market.daily_universe_members"] == 2
    assert summary["market.daily_ohlcv"] == 60
    assert "market.daily_technical_features" in summary
