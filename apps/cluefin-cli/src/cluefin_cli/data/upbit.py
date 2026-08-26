from datetime import datetime, timedelta, timezone
from time import sleep
from typing import List, Optional

import pandas as pd
import requests


class UpbitDataFetcher:
    """Fetches crypto OHLCV data from the Upbit public REST API (no auth required)."""

    BASE_URL = "https://api.upbit.com/v1"
    MAX_COUNT_PER_REQUEST = 200
    MAX_RETRIES = 6
    REQUEST_INTERVAL_SECONDS = 0.15

    def __init__(self):
        self.session = requests.Session()

    def get_hourly_candles(
        self,
        market: str = "KRW-BTC",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """Fetch 1-hour candles for a market, paginating backwards until `start`.

        Args:
            market: Upbit market id (e.g., "KRW-BTC")
            start: Inclusive lower bound (UTC-aware datetime). Defaults to 365 days before `end`.
            end: Exclusive upper bound (UTC-aware datetime). Defaults to now (UTC).

        Returns:
            DataFrame indexed by UTC candle time with columns
            [open, high, low, close, volume, quote_volume], ascending order.
        """
        if end is None:
            end = datetime.now(timezone.utc)
        if start is None:
            start = end - timedelta(days=365)

        by_candle_time: dict[pd.Timestamp, dict] = {}
        cursor = end

        while cursor > start:
            batch = self._fetch_candle_batch(market=market, unit=60, to=cursor)
            if not batch:
                break

            for candle in batch:
                candle_time = pd.to_datetime(candle["candle_date_time_utc"] + "Z")
                if candle_time < pd.Timestamp(start):
                    continue
                by_candle_time[candle_time] = {
                    "open": float(candle["opening_price"]),
                    "high": float(candle["high_price"]),
                    "low": float(candle["low_price"]),
                    "close": float(candle["trade_price"]),
                    "volume": float(candle["candle_acc_trade_volume"]),
                    "quote_volume": float(candle["candle_acc_trade_price"]),
                }

            oldest = pd.to_datetime(batch[-1]["candle_date_time_utc"] + "Z")
            if len(batch) < self.MAX_COUNT_PER_REQUEST or oldest <= pd.Timestamp(start):
                break
            cursor = oldest.to_pydatetime()
            sleep(self.REQUEST_INTERVAL_SECONDS)

        if not by_candle_time:
            return pd.DataFrame({"open": [], "high": [], "low": [], "close": [], "volume": [], "quote_volume": []})

        df = pd.DataFrame([{"date": time, **values} for time, values in sorted(by_candle_time.items())])
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)
        return df

    def _fetch_candle_batch(self, market: str, unit: int, to: datetime) -> List[dict]:
        url = f"{self.BASE_URL}/candles/minutes/{unit}"
        params = {
            "market": market,
            "to": to.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "count": self.MAX_COUNT_PER_REQUEST,
        }

        for attempt in range(self.MAX_RETRIES):
            response = self.session.get(url, params=params, timeout=30)
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", 2**attempt))
                sleep(retry_after)
                continue
            response.raise_for_status()
            return response.json()

        raise RuntimeError(f"Upbit rate limit exceeded after {self.MAX_RETRIES} retries")
