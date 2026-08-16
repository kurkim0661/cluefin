from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from cluefin_store.models import DailyOhlcv, DailyUniverseMember

RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
COLLECTED_AT = datetime(2026, 8, 14, 16, 0, 0)


def test_daily_universe_member_serializes_for_clickhouse() -> None:
    row = DailyUniverseMember(
        trade_date=date(2026, 8, 14),
        provider="toss",
        universe_name="manual_005930",
        universe_type="symbols",
        universe_params_json='{"symbols":["005930"]}',
        market_country="KR",
        rank=None,
        symbol="005930",
        name="삼성전자",
        selection_metric_name=None,
        selection_metric_value=None,
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
    )

    assert row.as_insert_row()["symbol"] == "005930"
    assert row.as_insert_row()["rank"] is None


def test_daily_ohlcv_accepts_decimal_prices() -> None:
    row = DailyOhlcv(
        trade_date=date(2026, 8, 14),
        provider="toss",
        symbol="005930",
        open=Decimal("100.0"),
        high=Decimal("110.0"),
        low=Decimal("95.0"),
        close=Decimal("108.0"),
        volume=1000,
        trading_amount=Decimal("108000.0"),
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
    )

    assert row.typical_price == Decimal("104.3333")
