from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import uuid4

import requests

from cluefin_store.ingestion import RankedSymbol
from cluefin_store.models import DailyOhlcv, _quantize_price

BASE_URL = "https://openapi.tossinvest.com"
# 1년치 백필은 종목당 캔들 페이지를 여러 번 넘기므로 한 번에 수백 건이 나간다. Toss는 그 속도에
# 429로 답하는데, 재시도가 없으면 백필 전체가 중간에 죽는다. 대기 시간은 배로 늘린다.
RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BASE_DELAY = 1.0
RATE_LIMIT_MAX_DELAY = 30.0


class TossMarketDataProvider:
    provider_name = "toss"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        base_url: str = BASE_URL,
        session: Any | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self._access_token: str | None = None

    @classmethod
    def from_env(cls) -> "TossMarketDataProvider":
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
        payload = self._get(
            "/api/v1/rankings",
            params={
                "type": ranking_type,
                "marketCountry": market_country,
                "duration": duration,
                "excludeInvestmentCaution": "true",
                "count": count,
            },
        )
        items = payload.get("result", {}).get("rankings", [])
        symbols = [item["symbol"] for item in items]
        names = self._fetch_stock_names(symbols)
        return [
            RankedSymbol(
                rank=int(item["rank"]),
                symbol=item["symbol"],
                name=names.get(item["symbol"], item["symbol"]),
                selection_metric_value=_decimal_or_none(item.get("tradingAmount")),
            )
            for item in items
        ]

    def fetch_daily_ohlcv(
        self, symbols: tuple[str, ...], start_date: date, end_date: date
    ) -> dict[str, list[DailyOhlcv]]:
        return {symbol: self._fetch_symbol_daily_ohlcv(symbol, start_date, end_date) for symbol in symbols}

    def _fetch_symbol_daily_ohlcv(self, symbol: str, start_date: date, end_date: date) -> list[DailyOhlcv]:
        before: str | None = f"{end_date.isoformat()}T23:59:59+09:00"
        rows: list[DailyOhlcv] = []
        while True:
            payload = self._get(
                "/api/v1/candles",
                params={
                    "symbol": symbol,
                    "interval": "1d",
                    "count": 200,
                    "before": before,
                    "adjusted": "true",
                },
            )
            result = payload.get("result", {})
            candles = result.get("candles", [])
            if not candles:
                break

            for candle in candles:
                row = _parse_candle(symbol, candle)
                if start_date <= row.trade_date <= end_date:
                    rows.append(row)

            oldest = min(_parse_date(candle["timestamp"]) for candle in candles)
            if oldest < start_date:
                break
            before = result.get("nextBefore")
            if not before:
                break

        return sorted({row.trade_date: row for row in rows}.values(), key=lambda row: row.trade_date)

    def _fetch_stock_names(self, symbols: list[str]) -> dict[str, str]:
        if not symbols:
            return {}
        payload = self._get("/api/v1/stocks", params={"symbols": ",".join(symbols)})
        result = payload.get("result", [])
        return {item["symbol"]: item.get("name") or item["symbol"] for item in result}

    def _get(self, path: str, *, params: dict[str, Any]) -> dict:
        for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
            response = self.session.get(
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self._token()}"},
                params=params,
                timeout=self.timeout,
            )
            if getattr(response, "status_code", 200) != 429 or attempt == RATE_LIMIT_MAX_RETRIES:
                response.raise_for_status()
                return response.json()
            self._sleep(_retry_delay(response, attempt))
        raise AssertionError("unreachable")  # pragma: no cover

    def _sleep(self, seconds: float) -> None:
        """테스트에서 실제로 기다리지 않도록 갈아끼우는 자리."""
        time.sleep(seconds)

    def _token(self) -> str:
        if self._access_token is not None:
            return self._access_token
        response = self.session.post(
            f"{self.base_url}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        self._access_token = payload["access_token"]
        return self._access_token


def _retry_delay(response: Any, attempt: int) -> float:
    """서버가 Retry-After로 알려준 시간을 우선 쓰고, 없으면 지수 백오프로 물러난다."""
    header = getattr(response, "headers", None) or {}
    retry_after = header.get("Retry-After")
    if retry_after is not None:
        try:
            return min(max(float(retry_after), 0.0), RATE_LIMIT_MAX_DELAY)
        except (TypeError, ValueError):
            # HTTP 날짜 형식으로 올 수도 있다. 해석에 실패하면 백오프로 넘어간다.
            pass
    return min(RATE_LIMIT_BASE_DELAY * (2**attempt), RATE_LIMIT_MAX_DELAY)


def _parse_candle(symbol: str, candle: dict) -> DailyOhlcv:
    trade_date = _parse_date(candle["timestamp"])
    open_price = _price(candle["openPrice"])
    high_price = _price(candle["highPrice"])
    low_price = _price(candle["lowPrice"])
    close_price = _price(candle["closePrice"])
    volume = int(Decimal(str(candle["volume"])).to_integral_value(rounding=ROUND_HALF_UP))
    return DailyOhlcv(
        trade_date=trade_date,
        provider="toss",
        symbol=symbol,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=volume,
        trading_amount=_quantize_price(close_price * Decimal(volume)),
        run_id=uuid4(),
        collected_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(value).date()


def _price(value: Any) -> Decimal:
    return _quantize_price(Decimal(str(value)))


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
