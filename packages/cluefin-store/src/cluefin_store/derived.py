"""이미 적재된 테이블에서 파생 테이블을 만든다.

거래일 달력·종목 마스터·환율은 별도 수집기가 필요하지 않다. 일봉·유니버스·지표 관측에 이미
들어 있는 사실을 다시 정리하는 것이라, 원천을 다시 긁지 않고 언제든 재생산할 수 있다.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from cluefin_store.models import ExchangeRate, MarketCalendarDay, StockMasterEntry

# 일봉에는 국가 컬럼이 없어 공급자로 가른다. 유니버스에 국가가 있으면 그 값을 우선한다.
PROVIDER_COUNTRIES = {"toss": "KR", "toss_us": "US"}
COUNTRY_CURRENCIES = {"KR": "KRW", "US": "USD"}
# 환율로 옮길 지표. (지표 ID, 기준통화, 상대통화)
EXCHANGE_RATE_INDICATORS = (("usd_krw", "USD", "KRW"),)

DERIVED_TABLES = ("market_calendar", "stock_master", "exchange_rates")


def derive_tables(
    store: Any,
    *,
    tables: tuple[str, ...] = DERIVED_TABLES,
    run_id: UUID | None = None,
    updated_at: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """고른 파생 테이블을 다시 만든다. 같은 키는 ReplacingMergeTree가 덮어쓴다."""
    unknown = [name for name in tables if name not in DERIVED_TABLES]
    if unknown:
        raise ValueError(f"알 수 없는 파생 테이블: {', '.join(unknown)}")
    stamp = updated_at or datetime.now()
    resolved_run_id = run_id or uuid4()
    builders = {
        "market_calendar": lambda: _market_calendar_rows(store, stamp),
        "stock_master": lambda: _stock_master_rows(store, stamp),
        "exchange_rates": lambda: _exchange_rate_rows(store, resolved_run_id, stamp),
    }
    summary: dict[str, Any] = {"run_id": str(resolved_run_id), "dry_run": dry_run, "rows": {}}
    for name in tables:
        rows = builders[name]()
        summary["rows"][f"market.{name}"] = len(rows)
        if rows and not dry_run:
            store.insert_records(f"market.{name}", rows)
    return summary


def _provider_countries(store: Any) -> dict[str, str]:
    rows = _query(
        store,
        """
        SELECT provider, argMax(market_country, trade_date) AS market_country
        FROM market.daily_universe_members FINAL
        GROUP BY provider
        """,
    )
    mapping = dict(PROVIDER_COUNTRIES)
    for row in rows:
        country = str(row.get("market_country") or "")
        if country:
            mapping[str(row["provider"])] = country
    return mapping


def _market_calendar_rows(store: Any, updated_at: datetime) -> list[MarketCalendarDay]:
    countries = _provider_countries(store)
    rows = _query(
        store,
        """
        SELECT provider, toString(trade_date) AS trade_date, count() AS symbols
        FROM market.daily_ohlcv FINAL
        GROUP BY provider, trade_date
        ORDER BY trade_date
        """,
    )
    # 같은 국가를 두 공급자가 채우면 한 날짜로 합친다.
    by_day: dict[tuple[str, date], int] = {}
    providers: dict[tuple[str, date], str] = {}
    for row in rows:
        country = countries.get(str(row["provider"]), "")
        if not country:
            continue
        key = (country, date.fromisoformat(str(row["trade_date"])))
        by_day[key] = by_day.get(key, 0) + int(row.get("symbols") or 0)
        providers.setdefault(key, str(row["provider"]))
    return [
        MarketCalendarDay(
            market_country=country,
            trade_date=trade_date,
            is_open=1,
            reason=f"{symbols}개 종목 일봉 존재",
            provider=providers[(country, trade_date)],
            updated_at=updated_at,
        )
        for (country, trade_date), symbols in sorted(by_day.items())
    ]


def _stock_master_rows(store: Any, updated_at: datetime) -> list[StockMasterEntry]:
    rows = _query(
        store,
        """
        SELECT provider, symbol,
               argMax(name, trade_date) AS name,
               argMax(market_country, trade_date) AS market_country,
               argMax(universe_name, trade_date) AS universe_name
        FROM market.daily_universe_members FINAL
        GROUP BY provider, symbol
        ORDER BY provider, symbol
        """,
    )
    countries = _provider_countries(store)
    entries: list[StockMasterEntry] = []
    for row in rows:
        provider = str(row["provider"])
        country = str(row.get("market_country") or countries.get(provider, ""))
        entries.append(
            StockMasterEntry(
                provider=provider,
                symbol=str(row["symbol"]),
                name=str(row.get("name") or ""),
                # 거래소·종목종류는 유니버스 응답에 없다. 모르는 값을 지어내지 않고 비워 둔다.
                market=None,
                market_country=country,
                currency=COUNTRY_CURRENCIES.get(country),
                security_type=None,
                updated_at=updated_at,
            )
        )
    return entries


def _exchange_rate_rows(store: Any, run_id: UUID, collected_at: datetime) -> list[ExchangeRate]:
    wanted = {indicator_id: (base, quote) for indicator_id, base, quote in EXCHANGE_RATE_INDICATORS}
    if not wanted:
        return []
    id_list = ", ".join(f"'{indicator_id}'" for indicator_id in wanted)
    rows = _query(
        store,
        f"""
        SELECT indicator_id, provider, toString(period) AS period, value
        FROM market.indicator_observations FINAL
        WHERE indicator_id IN ({id_list})
        ORDER BY period
        """,
    )
    rates: list[ExchangeRate] = []
    for row in rows:
        value = row.get("value")
        if value is None:
            continue
        base, quote = wanted[str(row["indicator_id"])]
        rates.append(
            ExchangeRate(
                trade_date=date.fromisoformat(str(row["period"])),
                provider=str(row.get("provider") or "derived"),
                base_currency=base,
                quote_currency=quote,
                rate=Decimal(str(value)),
                run_id=run_id,
                collected_at=collected_at,
            )
        )
    return rates


def _query(store: Any, sql: str) -> list[dict[str, Any]]:
    result = store.client().query(sql)
    columns = list(getattr(result, "column_names", []) or [])
    return [dict(zip(columns, row, strict=False)) for row in result.result_rows]
