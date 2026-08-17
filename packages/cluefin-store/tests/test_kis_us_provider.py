from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from cluefin_store.kis_us import KisUsMarketDataProvider


@dataclass(frozen=True)
class FakeMarketCapItem:
    symb: str
    excd: str
    name: str
    ename: str
    tomv: str
    tomv_org: str
    rank: str


@dataclass(frozen=True)
class FakeMarketCapBody:
    output2: list[FakeMarketCapItem]


@dataclass(frozen=True)
class FakeMarketCapResponse:
    body: FakeMarketCapBody


@dataclass(frozen=True)
class FakeCandle:
    xymd: str
    open: str
    high: str
    low: str
    clos: str
    tvol: str
    tamt: str


@dataclass(frozen=True)
class FakePeriodQuoteBody:
    output2: list[FakeCandle]


@dataclass(frozen=True)
class FakePeriodQuoteResponse:
    body: FakePeriodQuoteBody


class FakeOverseasMarketAnalysis:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_stock_market_cap_rank(
        self, keyb: str, auth: str, excd: str, vol_rang: str, curr_gb: str = "0"
    ) -> FakeMarketCapResponse:
        self.calls.append(excd)
        rows = {
            "NYS": [
                FakeMarketCapItem("BRK.B", "NYS", "Berkshire Hathaway", "", "800", "800000000000", "2"),
                FakeMarketCapItem("JPM", "NYS", "JPMorgan Chase", "", "500", "500000000000", "3"),
            ],
            "NAS": [
                FakeMarketCapItem("AAPL", "NAS", "Apple", "", "3000", "3000000000000", "1"),
                FakeMarketCapItem("MSFT", "NAS", "Microsoft", "", "2900", "2900000000000", "2"),
            ],
            "AMS": [
                FakeMarketCapItem("XYZ", "AMS", "", "Example ADR", "bad", "", "1"),
            ],
        }
        return FakeMarketCapResponse(FakeMarketCapBody(rows[excd]))


class FakeOverseasBasicQuote:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str, str, str]] = []

    def get_stock_period_quote(
        self, auth: str, excd: str, symb: str, gubn: str, bymd: str, modp: str, keyb: str = ""
    ) -> FakePeriodQuoteResponse:
        self.calls.append((auth, excd, symb, gubn, bymd, modp))
        return FakePeriodQuoteResponse(
            FakePeriodQuoteBody(
                [
                    FakeCandle("20260814", "100.10", "110.20", "99.90", "105.50", "12345", "1302579.75"),
                    FakeCandle("20260810", "90", "95", "89", "94", "100", "9400"),
                    FakeCandle("20250701", "80", "82", "79", "81", "50", "4050"),
                ]
            )
        )


class FakeKisClient:
    def __init__(self) -> None:
        self.overseas_market_analysis = FakeOverseasMarketAnalysis()
        self.overseas_basic_quote = FakeOverseasBasicQuote()


def test_fetch_ranked_symbols_merges_us_exchanges_and_sorts_by_market_cap() -> None:
    client = FakeKisClient()
    provider = KisUsMarketDataProvider(client=client)

    ranked = provider.fetch_ranked_symbols(
        ranking_type="MARKET_CAP",
        market_country="US",
        duration="current",
        count=3,
    )

    assert [item.symbol for item in ranked] == ["AAPL", "MSFT", "BRK.B"]
    assert [item.rank for item in ranked] == [1, 2, 3]
    assert ranked[0].selection_metric_value == Decimal("3000000000000")
    assert provider.exchange_by_symbol["BRK.B"] == "NYS"
    assert client.overseas_market_analysis.calls == ["NYS", "NAS", "AMS"]


def test_fetch_ranked_symbols_rejects_non_us_market_country() -> None:
    provider = KisUsMarketDataProvider(client=FakeKisClient())

    try:
        provider.fetch_ranked_symbols(
            ranking_type="MARKET_CAP",
            market_country="KR",
            duration="current",
            count=50,
        )
    except ValueError as exc:
        assert "market_country=US" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_fetch_daily_ohlcv_normalizes_kis_period_quotes() -> None:
    client = FakeKisClient()
    provider = KisUsMarketDataProvider(client=client)
    provider.exchange_by_symbol["AAPL"] = "NAS"

    rows = provider.fetch_daily_ohlcv(("AAPL",), date(2026, 8, 1), date(2026, 8, 17))

    candle = rows["AAPL"][0]
    assert candle.trade_date == date(2026, 8, 10)
    assert candle.provider == "kis"
    assert candle.symbol == "AAPL"
    assert candle.open == Decimal("90.0000")
    assert candle.close == Decimal("94.0000")
    assert candle.volume == 100
    assert candle.trading_amount == Decimal("9400.0000")
    assert rows["AAPL"][1].trade_date == date(2026, 8, 14)
    assert client.overseas_basic_quote.calls == [("", "NAS", "AAPL", "0", "20260817", "1")]
