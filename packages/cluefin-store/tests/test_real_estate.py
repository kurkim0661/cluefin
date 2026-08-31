from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

import pytest

from cluefin_store.real_estate import (
    REAL_ESTATE_CATALOG,
    EcosRealEstateProvider,
    collect_real_estate,
    week_start_of,
)

RUN_ID = UUID("00000000-0000-0000-0000-000000000031")
COLLECTED_AT = datetime(2026, 8, 31, 9, 0)


class FakeResponse:
    def __init__(self, payload) -> None:
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


class QueueSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        return self.responses.pop(0) if self.responses else FakeResponse({"StatisticSearch": {"row": []}})


class RecordingStore:
    def __init__(self) -> None:
        self.applied = 0
        self.inserts: dict[str, list] = {}

    def apply_schema(self) -> int:
        self.applied += 1
        return 1

    def insert_records(self, table: str, records) -> int:
        rows = list(records)
        self.inserts.setdefault(table, []).extend(rows)
        return len(rows)


def _spec(metric_id: str):
    return next(spec for spec in REAL_ESTATE_CATALOG if spec.metric_id == metric_id)


def test_catalog_covers_price_supply_and_capital_regions() -> None:
    metric_ids = {spec.metric_id for spec in REAL_ESTATE_CATALOG}

    assert {"house_sale_price_index", "house_jeonse_price_index", "unsold_housing"} <= metric_ids
    assert {spec.category for spec in REAL_ESTATE_CATALOG} == {"price", "supply"}
    sale = _spec("house_sale_price_index")
    assert [name for _, name in sale.property_items] == ["종합", "아파트", "연립다세대", "단독주택"]
    assert {name for _, name, _ in sale.region_items} >= {"서울", "경기", "인천", "수도권"}


def test_week_start_is_monday_of_the_observation_period() -> None:
    assert week_start_of(date(2026, 8, 31)) == date(2026, 8, 31)
    assert week_start_of(date(2026, 6, 1)) == date(2026, 6, 1)
    assert week_start_of(date(2026, 8, 30)) == date(2026, 8, 24)


def test_provider_expands_property_and_region_into_long_rows() -> None:
    session = QueueSession(
        [
            FakeResponse(
                {
                    "StatisticSearch": {
                        "list_total_count": 2,
                        "row": [
                            {
                                "TIME": "202605",
                                "DATA_VALUE": "102.7",
                                "UNIT_NAME": "2026.01=100",
                                "STAT_NAME": "유형별 주택매매가격지수",
                            },
                            {
                                "TIME": "202606",
                                "DATA_VALUE": "104.0",
                                "UNIT_NAME": "2026.01=100",
                                "STAT_NAME": "유형별 주택매매가격지수",
                            },
                        ],
                    }
                }
            )
        ]
    )
    provider = EcosRealEstateProvider(api_key="sample", session=session)
    spec = _spec("house_sale_price_index")
    single = type(spec)(
        **{
            **{field: getattr(spec, field) for field in spec.__slots__},
            "property_items": (("H69B", "아파트"),),
            "region_items": (("R70F", "서울", "수도권"),),
        }
    )

    rows = provider.collect((single,), date(2026, 5, 1), date(2026, 6, 30), RUN_ID, COLLECTED_AT)

    assert [row.value for row in rows] == [102.7, 104.0]
    assert rows[0].region == "서울"
    assert rows[0].region_tier == "수도권"
    assert rows[0].property_type == "아파트"
    assert rows[0].deal_type == "매매"
    assert rows[0].period == date(2026, 5, 1)
    assert rows[0].week_start == date(2026, 4, 27)
    assert "901Y144/M/202605/202606/H69B/R70F" in session.calls[0]


def test_provider_skips_blank_values_and_reports_errors() -> None:
    session = QueueSession(
        [
            FakeResponse({"StatisticSearch": {"list_total_count": 2, "row": [{"TIME": "202606", "DATA_VALUE": "-"}]}}),
            FakeResponse({"RESULT": {"CODE": "INFO-100", "MESSAGE": "인증키가 유효하지 않습니다."}}),
        ]
    )
    provider = EcosRealEstateProvider(api_key="sample", session=session)
    spec = _spec("unsold_housing")
    two_regions = type(spec)(
        **{
            **{field: getattr(spec, field) for field in spec.__slots__},
            "region_items": (("I410B", "서울", "수도권"), ("I410E", "인천", "수도권")),
        }
    )

    rows = provider.collect((two_regions,), date(2026, 6, 1), date(2026, 6, 30), RUN_ID, COLLECTED_AT)

    assert rows == []
    assert any("인천" in error for error in provider.errors)


def test_provider_treats_empty_window_as_no_rows() -> None:
    session = QueueSession([FakeResponse({"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}})])
    provider = EcosRealEstateProvider(api_key="sample", session=session)
    spec = _spec("land_price_change")
    single = type(spec)(
        **{
            **{field: getattr(spec, field) for field in spec.__slots__},
            "region_items": (("P65B", "서울", "수도권"),),
        }
    )

    assert provider.collect((single,), date(2026, 6, 1), date(2026, 6, 30), RUN_ID, COLLECTED_AT) == []
    assert provider.errors == []


def test_collect_writes_metrics_and_observations() -> None:
    class StubProvider:
        errors: list[str] = []

        def collect(self, specs, start_date, end_date, run_id, collected_at):
            spec = next(iter(specs))
            from cluefin_store.models import RealEstateObservation

            return [
                RealEstateObservation(
                    period=date(2026, 6, 1),
                    week_start=date(2026, 6, 1),
                    metric_id=spec.metric_id,
                    region="서울",
                    region_tier="수도권",
                    property_type="아파트",
                    deal_type=spec.deal_type,
                    frequency=spec.frequency,
                    unit=spec.unit,
                    provider=spec.provider,
                    value=104.0,
                    metadata_json="{}",
                    run_id=run_id,
                    collected_at=collected_at,
                )
            ]

    store = RecordingStore()

    summary = collect_real_estate(
        store,
        date(2026, 6, 1),
        date(2026, 6, 30),
        metrics=["house_sale_price_index"],
        provider=StubProvider(),
    )

    assert summary["observations"] == 1
    assert summary["by_metric"] == {"house_sale_price_index": 1}
    assert len(store.inserts["market.real_estate_metrics"]) == 1
    assert len(store.inserts["market.real_estate_observations"]) == 1


def test_collect_dry_run_touches_no_store() -> None:
    summary = collect_real_estate(None, date(2025, 9, 1), date(2026, 8, 31), dry_run=True)

    assert summary["dry_run"] is True
    assert summary["series"] > 50


def test_collect_strict_raises_on_provider_errors() -> None:
    class FailingProvider:
        errors = ["house_sale_price_index/서울: boom"]

        def collect(self, *args, **kwargs):
            return []

    with pytest.raises(RuntimeError, match="boom"):
        collect_real_estate(
            RecordingStore(),
            date(2026, 6, 1),
            date(2026, 6, 30),
            metrics=["house_sale_price_index"],
            provider=FailingProvider(),
            strict=True,
        )
