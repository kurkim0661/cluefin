from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

import pytest

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
                                "TIME": "20260826",
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
    # The policy rate uses the daily cycle so a rate decision shows up the same day.
    assert "722Y001/D/20260801/20260826/0101000" in session.calls[0][0]


def test_exchange_rates_come_from_the_bank_of_korea_daily_series() -> None:
    pairs = {spec.indicator_id: spec for spec in INDICATOR_CATALOG if spec.category == "fx" and spec.domain == "korea"}

    assert set(pairs) == {"usd_krw", "jpy_krw", "eur_krw", "cny_krw"}
    for spec in pairs.values():
        # FRED의 원/달러는 주 단위로 늦게 올라와 당일 값이 비어 있다. 매일 채우려면 ECOS 일별 시리즈여야 한다.
        assert spec.provider == "ecos"
        assert spec.frequency == "daily"
        assert spec.source_series.startswith("731Y001:D:")
    # 100엔당 고시라는 사실이 단위에 드러나야 파생 환율 테이블의 배수와 어긋나지 않는다.
    assert pairs["jpy_krw"].unit == "KRW/100JPY"


def test_ecos_provider_collects_todays_exchange_rate() -> None:
    session = QueueSession(
        [
            FakeResponse(
                payload={
                    "StatisticSearch": {
                        "row": [
                            {
                                "TIME": "20260907",
                                "DATA_VALUE": "1,355.2",
                                "UNIT_NAME": "원",
                                "ITEM_NAME1": "원/미국달러(매매기준율)",
                            }
                        ]
                    }
                }
            )
        ]
    )
    provider = EcosProvider(api_key="sample", session=session)

    rows = provider.collect((_spec("usd_krw"),), date(2026, 9, 1), date(2026, 9, 7), RUN_ID, COLLECTED_AT)

    # 고시값에는 천 단위 구분기호가 붙어 온다.
    assert rows[0].value == 1355.2
    assert rows[0].period == date(2026, 9, 7)
    assert rows[0].provider == "ecos"
    assert "731Y001/D/20260901/20260907/0000001" in session.calls[0][0]


def test_ecos_provider_treats_empty_window_as_no_rows() -> None:
    session = QueueSession(
        [FakeResponse(payload={"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}})]
    )
    provider = EcosProvider(api_key="sample", session=session)

    rows = provider.collect(
        (_spec("kr_policy_rate"),),
        date(2026, 8, 13),
        date(2026, 8, 27),
        RUN_ID,
        COLLECTED_AT,
    )

    assert rows == []


def test_ecos_provider_still_raises_on_real_errors() -> None:
    session = QueueSession(
        [FakeResponse(payload={"RESULT": {"CODE": "INFO-100", "MESSAGE": "인증키가 유효하지 않습니다."}})]
    )
    provider = EcosProvider(api_key="sample", session=session)

    with pytest.raises(RuntimeError, match="인증키"):
        provider.collect((_spec("kr_policy_rate"),), date(2026, 8, 1), date(2026, 8, 27), RUN_ID, COLLECTED_AT)


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


def test_binance_provider_reads_symbol_from_each_spec() -> None:
    session = QueueSession(
        [
            FakeResponse(payload=[{"fundingTime": 1787702400000, "fundingRate": "0.00002"}]),
            FakeResponse(payload=[{"timestamp": 1787702400000, "sumOpenInterestValue": "1234.5"}]),
            FakeResponse(payload=[{"fundingTime": 1787702400000, "fundingRate": "-0.00001"}]),
        ]
    )
    provider = BinanceFuturesProvider(session=session)

    rows = provider.collect(
        (_spec("eth_funding_rate"), _spec("eth_open_interest"), _spec("xrp_funding_rate")),
        date(2026, 8, 25),
        date(2026, 8, 26),
        RUN_ID,
        COLLECTED_AT,
    )

    assert {row.indicator_id for row in rows} == {"eth_funding_rate", "eth_open_interest", "xrp_funding_rate"}
    assert [call[1]["params"]["symbol"] for call in session.calls] == ["ETHUSDT", "ETHUSDT", "XRPUSDT"]


def test_coingecko_provider_resolves_dominance_for_every_coin() -> None:
    session = QueueSession(
        [
            FakeResponse(
                payload={
                    "data": {
                        "total_market_cap": {"usd": 2_600_000_000_000},
                        "total_volume": {"usd": 90_000_000_000},
                        "market_cap_percentage": {"btc": 59.2, "eth": 11.1, "xrp": 3.3},
                        "market_cap_change_percentage_24h_usd": -1.2,
                    }
                }
            )
        ]
    )
    provider = CoinGeckoProvider(session=session)

    rows = provider.collect(
        (_spec("btc_dominance"), _spec("eth_dominance"), _spec("xrp_dominance")),
        date(2026, 8, 26),
        date(2026, 8, 26),
        RUN_ID,
        COLLECTED_AT,
    )

    assert {row.indicator_id: row.value for row in rows} == {
        "btc_dominance": 59.2,
        "eth_dominance": 11.1,
        "xrp_dominance": 3.3,
    }


def test_coingecko_provider_reports_missing_dominance_without_dropping_others() -> None:
    session = QueueSession(
        [
            FakeResponse(
                payload={
                    "data": {
                        "total_market_cap": {"usd": 1.0},
                        "total_volume": {"usd": 1.0},
                        "market_cap_percentage": {"btc": 59.2},
                        "market_cap_change_percentage_24h_usd": -1.2,
                    }
                }
            )
        ]
    )
    provider = CoinGeckoProvider(session=session)

    rows = provider.collect(
        (_spec("btc_dominance"), _spec("xrp_dominance")),
        date(2026, 8, 26),
        date(2026, 8, 26),
        RUN_ID,
        COLLECTED_AT,
    )

    assert [row.indicator_id for row in rows] == ["btc_dominance"]
    assert any("xrp_dominance" in error for error in provider.errors)


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


def _observation_record(indicator_id: str, period: date, value: float):
    from cluefin_store.models import IndicatorObservation

    return IndicatorObservation(
        period=period,
        indicator_id=indicator_id,
        provider="coinmetrics",
        value=value,
        metadata_json="{}",
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
    )


def test_derived_metrics_cover_btc_eth_and_xrp() -> None:
    from cluefin_store.indicators import _derive_observations

    period = date(2026, 8, 27)
    observations = [
        _observation_record("btc_price_usd", period, 78_601.0),
        _observation_record("btc_mvrv", period, 2.0),
        _observation_record("eth_price_usd", period, 2_512.0),
        _observation_record("eth_mvrv", period, 1.25),
        _observation_record("xrp_price_usd", period, 1.5),
        _observation_record("xrp_mvrv", period, 1.0),
        _observation_record("eth_active_addresses", period, 900_000.0),
        _observation_record("eth_transactions", period, 1_800_000.0),
    ]

    derived = {
        row.indicator_id: row.value
        for row in _derive_observations(observations, INDICATOR_CATALOG, RUN_ID, COLLECTED_AT)
    }

    assert derived["btc_realized_price"] == 78_601.0 / 2.0
    assert derived["eth_realized_price"] == 2_512.0 / 1.25
    assert derived["xrp_realized_price"] == 1.5
    assert derived["eth_nupl"] == 1 - 1 / 1.25
    assert derived["xrp_nupl"] == 0.0
    assert derived["eth_active_addresses_ratio"] == 0.5


def test_derived_metrics_skip_assets_without_source_values() -> None:
    from cluefin_store.indicators import _derive_observations

    period = date(2026, 8, 27)
    observations = [
        _observation_record("btc_price_usd", period, 78_601.0),
        _observation_record("btc_mvrv", period, 2.0),
    ]

    derived = {row.indicator_id for row in _derive_observations(observations, INDICATOR_CATALOG, RUN_ID, COLLECTED_AT)}

    assert "btc_realized_price" in derived
    assert "eth_realized_price" not in derived
    assert "xrp_nupl" not in derived


def test_derived_metrics_tolerate_a_partial_catalog() -> None:
    from cluefin_store.indicators import _derive_observations

    # 환율만 도는 작업처럼 카탈로그의 일부만 넘어오면, 그 안에 없는 파생 지표는 건너뛰어야 한다.
    fx_only = tuple(spec for spec in INDICATOR_CATALOG if spec.category == "fx")
    observations = [_observation_record("usd_krw", date(2026, 9, 7), 1355.2)]

    assert _derive_observations(observations, fx_only, RUN_ID, COLLECTED_AT) == []


def test_ecos_monthly_series_still_uses_lookback_window() -> None:
    session = QueueSession(
        [
            FakeResponse(
                payload={
                    "StatisticSearch": {
                        "row": [
                            {"TIME": "202507", "DATA_VALUE": "100", "UNIT_NAME": "2020=100"},
                            {"TIME": "202607", "DATA_VALUE": "164.2", "UNIT_NAME": "2020=100"},
                        ]
                    }
                }
            )
        ]
    )
    provider = EcosProvider(api_key="sample", session=session)

    rows = provider.collect(
        (_spec("kr_export_value_index_yoy"),),
        date(2026, 8, 14),
        date(2026, 8, 28),
        RUN_ID,
        COLLECTED_AT,
    )

    # A YoY monthly series requests a full extra year before the 4-month lookback window.
    assert "403Y001/M/202504/202608/" in session.calls[0][0]
    assert rows[0].period == date(2026, 7, 1)
    assert round(rows[0].value, 2) == 64.20
