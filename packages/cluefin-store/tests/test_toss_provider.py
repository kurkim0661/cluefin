from datetime import date, datetime
from decimal import Decimal

from cluefin_store.toss import TossMarketDataProvider


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

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
