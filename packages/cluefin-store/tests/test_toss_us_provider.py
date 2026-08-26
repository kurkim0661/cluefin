from decimal import Decimal

from cluefin_store.toss_us import TossUsMarketCapProvider


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeTossSession:
    def post(self, url: str, data: dict, timeout: float) -> FakeResponse:
        return FakeResponse({"access_token": "token", "expires_in": 3600})

    def get(self, url: str, headers: dict, params: dict, timeout: float) -> FakeResponse:
        assert url.endswith("/api/v1/stocks")
        return FakeResponse(
            {
                "result": [
                    {"symbol": "MSFT", "name": "Microsoft"},
                    {"symbol": "AAPL", "name": "Apple"},
                ]
            }
        )


class FakeRankingSession:
    def get(self, url: str, params: dict, headers: dict, timeout: float) -> FakeResponse:
        return FakeResponse(
            {
                "data": {
                    "rows": [
                        {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "marketCap": "3000000"},
                        {"symbol": "BAD/W", "name": "Warrant", "marketCap": "9000000"},
                        {"symbol": "MSFT", "name": "Microsoft Corporation Common Stock", "marketCap": "4000000"},
                        {"symbol": "EMPTY", "name": "No market cap", "marketCap": ""},
                    ]
                }
            }
        )


def test_toss_us_provider_sorts_nasdaq_market_cap_and_uses_toss_names() -> None:
    provider = TossUsMarketCapProvider(
        client_id="client",
        client_secret="secret",
        session=FakeTossSession(),
        ranking_session=FakeRankingSession(),
    )

    rows = provider.fetch_ranked_symbols(
        ranking_type="MARKET_CAP",
        market_country="US",
        duration="current",
        count=2,
    )

    assert [(row.rank, row.symbol, row.name) for row in rows] == [
        (1, "MSFT", "Microsoft"),
        (2, "AAPL", "Apple"),
    ]
    assert rows[0].selection_metric_value == Decimal("4000000")


def test_toss_us_provider_rejects_non_market_cap_configuration() -> None:
    provider = TossUsMarketCapProvider(
        client_id="client",
        client_secret="secret",
        session=FakeTossSession(),
        ranking_session=FakeRankingSession(),
    )

    try:
        provider.fetch_ranked_symbols(
            ranking_type="MARKET_TRADING_AMOUNT",
            market_country="US",
            duration="1y",
            count=2,
        )
    except ValueError as exc:
        assert "MARKET_CAP" in str(exc)
    else:
        raise AssertionError("expected ValueError")
