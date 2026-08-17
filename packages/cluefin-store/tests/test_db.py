from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from cluefin_store.db import ClickHouseStore
from cluefin_store.models import DailyOhlcv

RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
COLLECTED_AT = datetime(2026, 8, 14, 16, 0, 0)


class FakeClient:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[list], list[str]]] = []

    def command(self, sql: str):
        return sql

    def insert(self, table: str, data: list[list], *, column_names: list[str]) -> None:
        self.inserts.append((table, data, column_names))


def test_insert_records_uses_dataclass_columns() -> None:
    client = FakeClient()
    store = ClickHouseStore(client=client)
    row = DailyOhlcv(
        trade_date=date(2026, 8, 14),
        provider="test",
        symbol="005930",
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=1000,
        trading_amount=Decimal("105000"),
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
    )

    inserted = store.insert_records("market.daily_ohlcv", [row])

    assert inserted == 1
    assert client.inserts[0][0] == "market.daily_ohlcv"
    assert "symbol" in client.inserts[0][2]
    assert client.inserts[0][1][0][client.inserts[0][2].index("symbol")] == "005930"
