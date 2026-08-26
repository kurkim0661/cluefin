from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from cluefin_store.ingestion import RankedSymbol
from cluefin_store.toss import BASE_URL, TossMarketDataProvider

NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


class TossUsMarketCapProvider(TossMarketDataProvider):
    """Use Nasdaq's public market-cap universe and Toss for names and candles.

    Toss supports US rankings by trading activity but does not expose a
    MARKET_CAP ranking type. Keeping this as a distinct provider makes that
    source boundary explicit in ClickHouse while retaining Toss price data.
    """

    provider_name = "toss_us"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        base_url: str = BASE_URL,
        session: Any | None = None,
        ranking_session: Any | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            session=session,
            timeout=timeout,
        )
        self.ranking_session = ranking_session or requests.Session()

    @classmethod
    def from_env(cls) -> "TossUsMarketCapProvider":
        client_id = os.getenv("TOSS_CLIENT_ID")
        client_secret = os.getenv("TOSS_CLIENT_SECRET")
        if not client_id:
            raise RuntimeError("TOSS_CLIENT_ID environment variable is required")
        if not client_secret:
            raise RuntimeError("TOSS_CLIENT_SECRET environment variable is required")
        return cls(client_id=client_id, client_secret=client_secret)

    def fetch_ranked_symbols(
        self,
        *,
        ranking_type: str,
        market_country: str,
        duration: str,
        count: int,
    ) -> list[RankedSymbol]:
        del duration
        if market_country != "US":
            raise ValueError("toss_us requires market_country=US")
        if ranking_type != "MARKET_CAP":
            raise ValueError("toss_us requires ranking_type=MARKET_CAP")
        response = self.ranking_session.get(
            NASDAQ_SCREENER_URL,
            params={"tableonly": "true", "limit": 5000, "offset": 0, "download": "true"},
            headers=NASDAQ_HEADERS,
            timeout=self.timeout,
        )
        response.raise_for_status()
        rows = response.json().get("data", {}).get("rows", [])
        ranked: list[tuple[Decimal, str, str]] = []
        for item in rows:
            symbol = str(item.get("symbol") or "").strip()
            market_cap = _market_cap(item.get("marketCap"))
            if not symbol or market_cap is None or market_cap <= 0 or not _tradable_symbol(symbol):
                continue
            ranked.append((market_cap, symbol, _clean_name(str(item.get("name") or symbol))))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected = ranked[:count]
        if len(selected) < count:
            raise RuntimeError(f"Nasdaq screener returned only {len(selected)} usable market-cap rows")

        toss_names = self._fetch_stock_names([item[1] for item in selected])
        return [
            RankedSymbol(
                rank=index,
                symbol=symbol,
                name=toss_names.get(symbol) or name,
                selection_metric_value=market_cap,
            )
            for index, (market_cap, symbol, name) in enumerate(selected, start=1)
        ]


def _market_cap(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("$", ""))
    except InvalidOperation:
        return None


def _tradable_symbol(symbol: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol)) and not any(
        marker in symbol for marker in ("^", "/", " ")
    )


def _clean_name(value: str) -> str:
    return re.sub(
        r"\s+(Common Stock|Ordinary Shares|American Depositary Shares?).*$",
        "",
        value.strip(),
        flags=re.IGNORECASE,
    ).strip()
