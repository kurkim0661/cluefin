from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from cluefin_store.indicators import (
    INDICATOR_CATALOG,
    BinanceFuturesProvider,
    CoinGeckoProvider,
    CoinMetricsProvider,
    DefiLlamaProvider,
    EcosProvider,
    FredCsvProvider,
    KoreaCustomsProvider,
    LicensedMetricsProvider,
    _dart_snapshot,
    catalog_summary,
    collect_market_indicators,
)

RUN_ID = UUID("00000000-0000-0000-0000-000000000026")
COLLECTED_AT = datetime(2026, 8, 26, 9, 30)


class FakeResponse:
    def __init__(self, *, text: str = "", payload=None, url: str = "https://example.test") -> None:
        self.text = text
        self._payload = payload
        self.url = url
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class QueueSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _spec(indicator_id: str):
    return next(item for item in INDICATOR_CATALOG if item.indicator_id == indicator_id)


def test_catalog_covers_macro_korea_equity_and_crypto_availability() -> None:
    summary = catalog_summary()

    assert set(summary["domains"]) >= {"global", "korea", "equity", "crypto"}
    assert summary["providers"]["fred"]["public"] >= 25
    assert summary["providers"]["licensed"]["licensed"] >= 3
    assert _spec("us_real_yield_10y").importance == 3
    assert _spec("btc_mvrv").provider == "coinmetrics"


def test_fred_provider_parses_values_and_skips_missing_rows() -> None:
    session = QueueSession(
        [FakeResponse(text="observation_date,DFII10\n2026-08-24,2.38\n2026-08-25,.\n2026-08-26,2.31\n")]
    )
    provider = FredCsvProvider(session=session)

    rows = provider.collect((_spec("us_real_yield_10y"),), date(2026, 8, 24), date(2026, 8, 26), RUN_ID, COLLECTED_AT)

    assert [row.value for row in rows] == [2.38, 2.31]
    assert rows[0].indicator_id == "us_real_yield_10y"
    assert session.calls[0][1]["params"]["id"] == "DFII10"


def test_defillama_provider_normalizes_stablecoin_and_tvl_history() -> None:
    session = QueueSession(
        [
            FakeResponse(payload=[{"date": "1787702400", "totalCirculatingUSD": {"peggedUSD": 250_000}}]),
            FakeResponse(payload=[{"date": 1787702400, "tvl": 125_000}]),
            FakeResponse(payload={"totalDataChart": [[1787702400, 12_000]]}),
            FakeResponse(payload={"totalDataChart": [[1787702400, 7_000]]}),
        ]
    )
    provider = DefiLlamaProvider(session=session)

    rows = provider.collect(
        (_spec("stablecoin_supply"), _spec("defi_tvl"), _spec("defi_fees"), _spec("defi_revenue")),
        date(2026, 8, 26),
        date(2026, 8, 26),
        RUN_ID,
        COLLECTED_AT,
    )

    assert {row.indicator_id: row.value for row in rows} == {
        "stablecoin_supply": 250_000,
        "defi_tvl": 125_000,
        "defi_fees": 12_000,
        "defi_revenue": 7_000,
    }


def test_coinmetrics_provider_maps_asset_metrics_to_indicator_ids() -> None:
    session = QueueSession(
        [
            FakeResponse(
                payload={
                    "data": [
                        {
                            "asset": "btc",
                            "time": "2026-08-26T00:00:00.000000000Z",
                            "CapMVRVCur": "1.42",
                            "AdrActCnt": "721893",
                        }
                    ]
                }
            ),
            FakeResponse(payload={"prices": [[1787702400000, 4624.35]]}),
        ]
    )
    provider = CoinMetricsProvider(session=session)

    rows = provider.collect(
        (_spec("btc_mvrv"), _spec("btc_active_addresses")),
        date(2026, 8, 26),
        date(2026, 8, 26),
        RUN_ID,
        COLLECTED_AT,
    )

    assert {row.indicator_id: row.value for row in rows} == {
        "btc_mvrv": 1.42,
        "btc_active_addresses": 721893,
    }


def test_coingecko_provider_builds_current_global_snapshot() -> None:
    session = QueueSession(
        [
            FakeResponse(
                payload={
                    "data": {
                        "total_market_cap": {"usd": 2_600_000_000_000},
                        "total_volume": {"usd": 90_000_000_000},
                        "market_cap_percentage": {"btc": 53.2},
                        "market_cap_change_percentage_24h_usd": -1.2,
                    }
                }
            ),
            FakeResponse(payload={"prices": [[1787702400000, 4624.35]]}),
        ]
    )
    provider = CoinGeckoProvider(session=session)
    specs = tuple(item for item in INDICATOR_CATALOG if item.provider == "coingecko")

    rows = provider.collect(specs, date(2026, 8, 26), date(2026, 8, 26), RUN_ID, COLLECTED_AT)

    assert len(rows) == 5
    assert next(row.value for row in rows if row.indicator_id == "btc_dominance") == 53.2
    assert next(row.value for row in rows if row.indicator_id == "gold") == 4624.35


def test_ecos_provider_normalizes_bank_of_korea_observations() -> None:
    session = QueueSession(
        [
            FakeResponse(
                payload={
                    "StatisticSearch": {
                        "row": [
                            {
                                "TIME": "202608",
                                "DATA_VALUE": "2.75",
                                "UNIT_NAME": "연%",
                                "ITEM_NAME1": "한국은행 기준금리",
                            }
                        ]
                    }
                }
            )
        ]
    )
    provider = EcosProvider(api_key="sample", session=session)

    rows = provider.collect(
        (_spec("kr_policy_rate"),),
        date(2026, 8, 1),
        date(2026, 8, 26),
        RUN_ID,
        COLLECTED_AT,
    )

    assert rows[0].value == 2.75
    assert "722Y001/M/202608/202608/0101000" in session.calls[0][0]


def test_binance_provider_normalizes_funding_and_open_interest() -> None:
    session = QueueSession(
        [
            FakeResponse(
                payload=[
                    {"fundingTime": 1787673600000, "fundingRate": "0.0001"},
                    {"fundingTime": 1787702400000, "fundingRate": "0.0002"},
                ]
            ),
            FakeResponse(
                payload=[
                    {
                        "timestamp": 1787702400000,
                        "sumOpenInterestValue": "8368057784.967",
                    }
                ]
            ),
        ]
    )
    provider = BinanceFuturesProvider(session=session)

    rows = provider.collect(
        (_spec("crypto_funding_rate"), _spec("crypto_open_interest")),
        date(2026, 8, 25),
        date(2026, 8, 26),
        RUN_ID,
        COLLECTED_AT,
    )

    values = {row.indicator_id: row.value for row in rows}
    assert values["crypto_funding_rate"] == 0.02
    assert values["crypto_open_interest"] == 8368057784.967


def test_licensed_provider_uses_pat_and_normalized_contract() -> None:
    session = QueueSession(
        [
            FakeResponse(
                payload={
                    "data": [
                        {
                            "indicator_id": "btc_spot_etf_flow",
                            "period": "2026-08-26",
                            "value": 125000000,
                            "vendor": "contracted-feed",
                        }
                    ]
                }
            )
        ]
    )
    provider = LicensedMetricsProvider(url="https://vendor.test/metrics", pat="secret-pat", session=session)

    rows = provider.collect(
        (_spec("btc_spot_etf_flow"),),
        date(2026, 8, 1),
        date(2026, 8, 26),
        RUN_ID,
        COLLECTED_AT,
    )

    assert rows[0].value == 125000000
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer secret-pat"


def test_customs_provider_discovers_and_parses_first_twenty_day_release() -> None:
    list_html = """
    <a class="nttInfoBtn" data-id="10173983" data-url="release-token"
       title="2026년 8월 1일 ~ 8월 20일 수출입 현황 [잠정치]">release</a>
    """
    detail_html = "<p>동기간 수출은 552억 달러로 전년동기대비 56.0% 증가했습니다.</p>"
    session = QueueSession([FakeResponse(text=list_html), FakeResponse(text=detail_html)])
    provider = KoreaCustomsProvider(session=session, workers=1)

    rows = provider.collect(
        (_spec("kr_exports_20d"),),
        date(2026, 8, 1),
        date(2026, 8, 26),
        RUN_ID,
        COLLECTED_AT,
    )

    assert rows[0].period == date(2026, 8, 20)
    assert rows[0].value == 56.0
    assert '"ntt_sn": "10173983"' in rows[0].metadata_json


def test_dart_snapshot_calculates_growth_profitability_and_leverage() -> None:
    snapshot = _dart_snapshot(
        [
            {"account_nm": "매출액", "thstrm_amount": "120", "frmtrm_amount": "100"},
            {"account_nm": "영업이익", "thstrm_amount": "20", "frmtrm_amount": "10"},
            {"account_nm": "당기순이익", "thstrm_amount": "12", "frmtrm_amount": "8"},
            {"account_nm": "부채총계", "thstrm_amount": "80", "frmtrm_amount": "70"},
            {"account_nm": "자본총계", "thstrm_amount": "100", "frmtrm_amount": "90"},
        ]
    )

    assert snapshot == {
        "revenue_growth": 20.0,
        "operating_profit_growth": 100.0,
        "roe": 12.0,
        "debt_ratio": 80.0,
    }


class FakeStore:
    def __init__(self) -> None:
        self.inserted: dict[str, list] = {}

    def insert_records(self, table: str, records: list) -> int:
        self.inserted[table] = list(records)
        return len(records)


class FixedProvider:
    provider_name = "fred"

    def collect(self, specs, start_date, end_date, run_id, collected_at):
        assert specs
        return []


def test_pipeline_always_writes_full_catalog_and_provider_summary() -> None:
    store = FakeStore()

    summary = collect_market_indicators(
        store=store,
        providers=(FixedProvider(),),
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 26),
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
    )

    assert summary["definitions"] == len(INDICATOR_CATALOG)
    assert summary["providers"]["fred"] == 0
    assert "market.indicator_definitions" in store.inserted
    assert "market.indicator_observations" in store.inserted
