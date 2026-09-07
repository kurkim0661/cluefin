from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from cluefin_store.derived import DERIVED_TABLES, derive_tables

UPDATED_AT = datetime(2026, 9, 1, 9, 0, 0)


class FakeResult:
    def __init__(self, rows: list[tuple], columns: list[str]) -> None:
        self.result_rows = rows
        self.column_names = columns


class FakeClient:
    def __init__(self, responses: list[tuple[str, FakeResult]]) -> None:
        self.responses = responses
        self.queries: list[str] = []

    def query(self, sql: str) -> FakeResult:
        self.queries.append(sql)
        for marker, result in self.responses:
            if marker in sql:
                return result
        return FakeResult([], [])


class FakeStore:
    def __init__(self, client: FakeClient) -> None:
        self._client = client
        self.inserted: dict[str, list] = {}

    def client(self) -> FakeClient:
        return self._client

    def insert_records(self, table: str, records: list) -> int:
        self.inserted.setdefault(table, []).extend(records)
        return len(records)


def _store() -> FakeStore:
    return FakeStore(
        FakeClient(
            [
                (
                    "GROUP BY provider\n",
                    FakeResult([("toss", "KR"), ("toss_us", "US")], ["provider", "market_country"]),
                ),
                (
                    "FROM market.daily_ohlcv",
                    FakeResult(
                        [
                            ("toss", "2026-08-31", 50),
                            ("toss", "2026-09-01", 50),
                            ("toss_us", "2026-09-01", 48),
                            ("unknown_provider", "2026-09-01", 3),
                        ],
                        ["provider", "trade_date", "symbols"],
                    ),
                ),
                (
                    "GROUP BY provider, symbol",
                    FakeResult(
                        [
                            ("toss", "005930", "삼성전자", "KR", "kr_top50"),
                            ("toss_us", "AAPL", "Apple", "US", "us_top50"),
                        ],
                        ["provider", "symbol", "name", "market_country", "universe_name"],
                    ),
                ),
                (
                    "FROM market.indicator_observations",
                    FakeResult(
                        [
                            ("usd_krw", "fred", "2026-08-27", 1375.5),
                            ("usd_krw", "fred", "2026-08-28", 1379.41),
                            ("usd_krw", "fred", "2026-08-31", None),
                        ],
                        ["indicator_id", "provider", "period", "value"],
                    ),
                ),
            ]
        )
    )


def test_derive_tables_builds_all_three_from_existing_rows() -> None:
    store = _store()

    summary = derive_tables(store, updated_at=UPDATED_AT)

    assert summary["rows"] == {
        "market.market_calendar": 3,
        "market.stock_master": 2,
        "market.exchange_rates": 2,
    }
    assert set(store.inserted) == {"market.market_calendar", "market.stock_master", "market.exchange_rates"}


def test_market_calendar_merges_providers_per_country_and_skips_unknown() -> None:
    store = _store()

    derive_tables(store, tables=("market_calendar",), updated_at=UPDATED_AT)

    days = {(row.market_country, row.trade_date): row for row in store.inserted["market.market_calendar"]}
    assert sorted(days) == [
        ("KR", date(2026, 8, 31)),
        ("KR", date(2026, 9, 1)),
        ("US", date(2026, 9, 1)),
    ]
    assert days[("US", date(2026, 9, 1))].is_open == 1
    assert "48개" in days[("US", date(2026, 9, 1))].reason
    # 국가를 알 수 없는 공급자는 달력에 넣지 않는다.
    assert all(row.market_country in {"KR", "US"} for row in store.inserted["market.market_calendar"])


def test_stock_master_fills_currency_and_leaves_unknown_fields_empty() -> None:
    store = _store()

    derive_tables(store, tables=("stock_master",), updated_at=UPDATED_AT)

    entries = {row.symbol: row for row in store.inserted["market.stock_master"]}
    assert entries["005930"].name == "삼성전자"
    assert entries["005930"].currency == "KRW"
    assert entries["AAPL"].currency == "USD"
    # 거래소·종목종류는 원천에 없으므로 지어내지 않는다.
    assert entries["005930"].market is None
    assert entries["005930"].security_type is None
    assert entries["005930"].updated_at == UPDATED_AT


def test_exchange_rates_copy_indicator_values_and_drop_nulls() -> None:
    store = _store()

    derive_tables(store, tables=("exchange_rates",), updated_at=UPDATED_AT)

    rates = store.inserted["market.exchange_rates"]
    assert [row.trade_date for row in rates] == [date(2026, 8, 27), date(2026, 8, 28)]
    assert rates[-1].rate == Decimal("1379.41")
    assert (rates[-1].base_currency, rates[-1].quote_currency) == ("USD", "KRW")
    assert rates[-1].provider == "fred"


def test_dry_run_counts_without_inserting() -> None:
    store = _store()

    summary = derive_tables(store, dry_run=True, updated_at=UPDATED_AT)

    assert summary["dry_run"] is True
    assert summary["rows"]["market.stock_master"] == 2
    assert store.inserted == {}


def test_unknown_table_is_rejected() -> None:
    with pytest.raises(ValueError):
        derive_tables(_store(), tables=("market_calendar", "nope"))


def test_derived_table_list_matches_builders() -> None:
    store = _store()

    summary = derive_tables(store, tables=DERIVED_TABLES, dry_run=True)

    assert set(summary["rows"]) == {f"market.{name}" for name in DERIVED_TABLES}
