from datetime import date, datetime
from decimal import Decimal

import pytest
import requests

from cluefin_store.toss import RATE_LIMIT_MAX_RETRIES, TossMarketDataProvider


class FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200, headers: dict | None = None) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error", response=self)

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[tuple[str, dict, dict]] = []

    def post(self, url: str, data: dict, timeout: float) -> FakeResponse:
        self.posts.append((url, data))
        return FakeResponse({"access_token": "token-1", "token_type": "Bearer", "expires_in": 3600})

    def get(self, url: str, headers: dict, params: dict, timeout: float) -> FakeResponse:
        self.gets.append((url, headers, params))
        if url.endswith("/api/v1/rankings"):
            return FakeResponse(
                {
                    "result": {
                        "rankings": [
                            {
                                "rank": 1,
                                "symbol": "005930",
                                "tradingAmount": "1000000",
                            }
                        ]
                    }
                }
            )
        if url.endswith("/api/v1/stocks"):
            return FakeResponse({"result": [{"symbol": "005930", "name": "삼성전자"}]})
        return FakeResponse(
            {
                "result": {
                    "candles": [
                        {
                            "timestamp": "2026-08-14T00:00:00+09:00",
                            "openPrice": "100",
                            "highPrice": "110",
                            "lowPrice": "90",
                            "closePrice": "105",
                            "volume": "1000",
                        }
                    ],
                    "nextBefore": None,
                }
            }
        )


def test_toss_provider_fetches_ranked_symbols_with_stock_names() -> None:
    session = FakeSession()
    provider = TossMarketDataProvider(client_id="client", client_secret="secret", session=session)

    ranked = provider.fetch_ranked_symbols(
        ranking_type="MARKET_TRADING_AMOUNT",
        market_country="KR",
        duration="1y",
        count=50,
    )

    assert ranked[0].symbol == "005930"
    assert ranked[0].name == "삼성전자"
    assert ranked[0].selection_metric_value == Decimal("1000000")
    assert session.posts[0][1]["grant_type"] == "client_credentials"


def test_toss_provider_fetches_daily_ohlcv() -> None:
    session = FakeSession()
    provider = TossMarketDataProvider(client_id="client", client_secret="secret", session=session)

    candles = provider.fetch_daily_ohlcv(("005930",), date(2026, 8, 1), date(2026, 8, 16))

    row = candles["005930"][0]
    assert row.trade_date == date(2026, 8, 14)
    assert row.close == Decimal("105.0000")
    assert row.collected_at <= datetime.now()


class ThrottledSession(FakeSession):
    """앞의 몇 번은 429로 답하고 그 뒤부터 정상 응답을 주는 세션."""

    def __init__(self, throttled_calls: int, *, retry_after: str | None = None) -> None:
        super().__init__()
        self.remaining = throttled_calls
        self.retry_after = retry_after

    def get(self, url: str, headers: dict, params: dict, timeout: float) -> FakeResponse:
        if self.remaining > 0:
            self.remaining -= 1
            self.gets.append((url, headers, params))
            return FakeResponse(
                {}, status_code=429, headers={"Retry-After": self.retry_after} if self.retry_after else {}
            )
        return super().get(url, headers=headers, params=params, timeout=timeout)


def _no_sleep(provider: TossMarketDataProvider) -> list[float]:
    slept: list[float] = []
    provider._sleep = slept.append
    return slept


def test_toss_provider_retries_after_a_rate_limit_instead_of_failing_the_backfill() -> None:
    session = ThrottledSession(2)
    provider = TossMarketDataProvider(client_id="client", client_secret="secret", session=session)
    slept = _no_sleep(provider)

    candles = provider.fetch_daily_ohlcv(("005930",), date(2026, 8, 1), date(2026, 8, 16))

    assert candles["005930"][0].trade_date == date(2026, 8, 14)
    # 1초에서 시작해 시도마다 두 배로 물러난다.
    assert slept == [1.0, 2.0]


def test_toss_provider_honours_the_retry_after_header() -> None:
    session = ThrottledSession(1, retry_after="7")
    provider = TossMarketDataProvider(client_id="client", client_secret="secret", session=session)
    slept = _no_sleep(provider)

    provider.fetch_daily_ohlcv(("005930",), date(2026, 8, 1), date(2026, 8, 16))

    # 서버가 알려준 대기 시간이 자체 백오프보다 우선한다.
    assert slept == [7.0]


def test_toss_provider_gives_up_after_the_retry_budget() -> None:
    session = ThrottledSession(RATE_LIMIT_MAX_RETRIES + 1)
    provider = TossMarketDataProvider(client_id="client", client_secret="secret", session=session)
    _no_sleep(provider)

    # 끝없이 재시도하지 않는다. 계속 막히면 백필을 조용히 비우지 말고 실패시킨다.
    with pytest.raises(requests.HTTPError):
        provider.fetch_daily_ohlcv(("005930",), date(2026, 8, 1), date(2026, 8, 16))
