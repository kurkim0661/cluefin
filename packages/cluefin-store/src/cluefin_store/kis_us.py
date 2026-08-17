from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from cluefin_store.ingestion import RankedSymbol
from cluefin_store.models import DailyOhlcv, _quantize_price

US_EXCHANGES: tuple[str, ...] = ("NYS", "NAS", "AMS")


class KisUsMarketDataProvider:
    provider_name = "kis"

    def __init__(self, *, client: Any) -> None:
        self.client = client
        self.exchange_by_symbol: dict[str, str] = {}

    @classmethod
    def from_env(cls) -> "KisUsMarketDataProvider":
        from cluefin_openapi.client_factory import BrokerClientFactory

        return cls(client=BrokerClientFactory().create_kis())

    def fetch_ranked_symbols(
        self,
        *,
        ranking_type: str,
        market_country: str,
        duration: str,
        count: int,
    ) -> list[RankedSymbol]:
        if market_country.upper() != "US":
            raise ValueError("KisUsMarketDataProvider only supports market_country=US")
        if ranking_type.upper() != "MARKET_CAP":
            raise ValueError("KisUsMarketDataProvider only supports ranking_type=MARKET_CAP")

        rows: list[tuple[str, str, str, Decimal]] = []
        self.exchange_by_symbol.clear()
        for exchange in US_EXCHANGES:
            response = self.client.overseas_market_analysis.get_stock_market_cap_rank(
                keyb="",
                auth="",
                excd=exchange,
                vol_rang="0",
            )
            for item in getattr(response.body, "output2", []):
                symbol = str(getattr(item, "symb", "") or "").strip()
                metric = _decimal_or_none(getattr(item, "tomv_org", None)) or _decimal_or_none(
                    getattr(item, "tomv", None)
                )
                if not symbol or metric is None:
                    continue
                name = str(getattr(item, "name", "") or getattr(item, "ename", "") or symbol).strip()
                item_exchange = str(getattr(item, "excd", "") or exchange).strip() or exchange
                rows.append((symbol, name or symbol, item_exchange, metric))

        rows.sort(key=lambda row: row[3], reverse=True)
        selected = rows[:count]
        if not selected:
            raise ValueError("KIS US market-cap ranking returned no parseable rows")

        ranked: list[RankedSymbol] = []
        for index, (symbol, name, exchange, metric) in enumerate(selected, start=1):
            self.exchange_by_symbol[symbol] = exchange
            ranked.append(
                RankedSymbol(
                    rank=index,
                    symbol=symbol,
                    name=name,
                    selection_metric_value=metric,
                )
            )
        return ranked

    def fetch_daily_ohlcv(
        self,
        symbols: tuple[str, ...],
        start_date: date,
        end_date: date,
    ) -> dict[str, list[DailyOhlcv]]:
        result: dict[str, list[DailyOhlcv]] = {}
        for symbol in symbols:
            exchange = self.exchange_by_symbol.get(symbol)
            if not exchange:
                raise ValueError(f"KIS exchange code is missing for symbol={symbol}")
            response = self.client.overseas_basic_quote.get_stock_period_quote(
                auth="",
                excd=exchange,
                symb=symbol,
                gubn="0",
                bymd=end_date.strftime("%Y%m%d"),
                modp="1",
            )
            candles = [_parse_period_quote_candle(symbol, candle) for candle in getattr(response.body, "output2", [])]
            result[symbol] = sorted(
                (candle for candle in candles if start_date <= candle.trade_date <= end_date),
                key=lambda candle: candle.trade_date,
            )
        return result


def _parse_period_quote_candle(symbol: str, candle: Any) -> DailyOhlcv:
    trade_date = datetime.strptime(str(candle.xymd), "%Y%m%d").date()
    open_price = _price(candle.open)
    high_price = _price(candle.high)
    low_price = _price(candle.low)
    close_price = _price(candle.clos)
    volume = int(Decimal(str(candle.tvol)).to_integral_value(rounding=ROUND_HALF_UP))
    trading_amount = _decimal_or_none(getattr(candle, "tamt", None))
    if trading_amount is None:
        trading_amount = close_price * Decimal(volume)
    return DailyOhlcv(
        trade_date=trade_date,
        provider=KisUsMarketDataProvider.provider_name,
        symbol=symbol,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=volume,
        trading_amount=_quantize_price(trading_amount),
        run_id=uuid4(),
        collected_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


def _price(value: Any) -> Decimal:
    parsed = _decimal_or_none(value)
    if parsed is None:
        raise ValueError(f"missing KIS price value: {value!r}")
    return _quantize_price(parsed)


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None
