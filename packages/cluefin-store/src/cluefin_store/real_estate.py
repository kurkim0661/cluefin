"""수도권 중심 부동산 시계열 수집.

한국은행 ECOS가 한국부동산원·KB·국가데이터처 부동산 통계를 인증키 없이 중계하므로
지역 × 주택유형 × 거래유형 조합을 하나의 롱 포맷 팩트 테이블로 정규화한다.
관측 주기는 원천 그대로 저장하고, 분석 화면이 주 단위로 묶을 수 있도록 week_start를 함께 채운다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable
from uuid import UUID, uuid4

import requests

from cluefin_store.models import RealEstateMetric, RealEstateObservation

ECOS_BASE_URL = "https://ecos.bok.or.kr/api/StatisticSearch"
ECOS_SOURCE_URL = "https://ecos.bok.or.kr/"
CAPITAL_REGIONS = ("전국", "수도권", "지방", "서울", "경기", "인천")


@dataclass(frozen=True, slots=True)
class RealEstateMetricSpec:
    metric_id: str
    name_ko: str
    name_en: str
    category: str
    deal_type: str
    unit: str
    frequency: str
    higher_is: str
    description_ko: str
    interpretation_ko: str
    stat_code: str
    cycle: str
    property_items: tuple[tuple[str, str], ...]
    region_items: tuple[tuple[str, str, str], ...]
    provider: str = "ecos"

    def as_metric(self, updated_at: datetime) -> RealEstateMetric:
        return RealEstateMetric(
            metric_id=self.metric_id,
            name_ko=self.name_ko,
            name_en=self.name_en,
            category=self.category,
            deal_type=self.deal_type,
            unit=self.unit,
            frequency=self.frequency,
            higher_is=self.higher_is,
            description_ko=self.description_ko,
            interpretation_ko=self.interpretation_ko,
            provider=self.provider,
            source_series=f"{self.stat_code}:{self.cycle}",
            source_url=ECOS_SOURCE_URL,
            updated_at=updated_at,
        )


# 지역 코드는 통계표마다 다르므로 (코드, 표시명, 권역구분)으로 함께 들고 다닌다.
_TYPE_INDEX_REGIONS = (
    ("R70A", "전국", "전국"),
    ("R70B", "수도권", "수도권"),
    ("R70C", "지방", "지방"),
    ("R70F", "서울", "수도권"),
    ("R70G", "경기", "수도권"),
    ("R70H", "인천", "수도권"),
)
_TYPE_INDEX_PROPERTIES = (
    ("H69A", "종합"),
    ("H69B", "아파트"),
    ("H69C", "연립다세대"),
    ("H69D", "단독주택"),
)
_REAL_DEAL_REGIONS = (
    ("100", "전국", "전국"),
    ("200", "서울", "수도권"),
    ("210", "서울 도심권", "서울권역"),
    ("220", "서울 동북권", "서울권역"),
    ("230", "서울 동남권", "서울권역"),
    ("240", "서울 서북권", "서울권역"),
    ("250", "서울 서남권", "서울권역"),
    ("300", "수도권", "수도권"),
    ("400", "지방", "지방"),
    ("700", "인천", "수도권"),
    ("C00", "경기", "수도권"),
)
_UNSOLD_REGIONS = (
    ("I410A", "전국", "전국"),
    ("I410R", "수도권", "수도권"),
    ("I410B", "서울", "수도권"),
    ("I410E", "인천", "수도권"),
    ("I410I", "경기", "수도권"),
)
_PERMIT_REGIONS = (
    ("ALL", "전국", "전국"),
    ("SEO", "서울", "수도권"),
    ("INC", "인천", "수도권"),
    ("GYE", "경기", "수도권"),
)
_LAND_REGIONS = (
    ("P65A", "전국", "전국"),
    ("P65B", "서울", "수도권"),
    ("P65E", "인천", "수도권"),
    ("P65J", "경기", "수도권"),
)
_ALL_PROPERTY = (("", "전체"),)

REAL_ESTATE_CATALOG: tuple[RealEstateMetricSpec, ...] = (
    RealEstateMetricSpec(
        "house_sale_price_index",
        "주택 매매가격지수",
        "House Sale Price Index",
        "price",
        "매매",
        "2026.01=100",
        "monthly",
        "risk_on",
        "한국부동산원 전국주택가격동향조사의 유형별 매매가격지수",
        "지수 자체보다 전월·전년 대비 변화율과 지역 간 격차를 함께 봅니다.",
        "901Y144",
        "M",
        _TYPE_INDEX_PROPERTIES,
        _TYPE_INDEX_REGIONS,
    ),
    RealEstateMetricSpec(
        "house_jeonse_price_index",
        "주택 전세가격지수",
        "House Jeonse Price Index",
        "price",
        "전세",
        "2026.01=100",
        "monthly",
        "context",
        "한국부동산원 전국주택가격동향조사의 유형별 전세가격지수",
        "전세가 매매보다 먼저 움직이는 국면이 많아 선행 신호로 참고합니다.",
        "901Y145",
        "M",
        _TYPE_INDEX_PROPERTIES,
        _TYPE_INDEX_REGIONS,
    ),
    RealEstateMetricSpec(
        "house_monthly_rent_index",
        "주택 월세통합가격지수",
        "House Monthly Rent Index",
        "price",
        "월세",
        "2026.01=100",
        "monthly",
        "context",
        "한국부동산원 전국주택가격동향조사의 유형별 월세통합가격지수",
        "전세의 월세 전환 흐름을 볼 때 전세지수와 함께 비교합니다.",
        "901Y146",
        "M",
        _TYPE_INDEX_PROPERTIES,
        _TYPE_INDEX_REGIONS,
    ),
    # 현행 지수는 2021-06부터라 10년 비교가 불가능하다. 기준월이 다른 구지수는 값 자체가
    # 이어지지 않으므로 이어붙이지 않고 별도 지표로 두고, 화면의 재지수화 변환으로 비교한다.
    RealEstateMetricSpec(
        "house_sale_price_index_long",
        "주택 매매가격지수 (장기·2021.06 기준)",
        "House Sale Price Index (2021.06 base)",
        "price",
        "매매",
        "2021.06=100",
        "monthly",
        "risk_on",
        "2003-11부터 2025-03까지 이어지는 구 기준월 매매가격지수",
        "현행 지수와 기준월이 달라 값을 직접 이어붙일 수 없습니다. 장기 추세는 재지수화해서 비교하세요.",
        "901Y093",
        "M",
        _TYPE_INDEX_PROPERTIES,
        _TYPE_INDEX_REGIONS,
    ),
    RealEstateMetricSpec(
        "house_jeonse_price_index_long",
        "주택 전세가격지수 (장기·2021.06 기준)",
        "House Jeonse Price Index (2021.06 base)",
        "price",
        "전세",
        "2021.06=100",
        "monthly",
        "context",
        "2003-11부터 2025-03까지 이어지는 구 기준월 전세가격지수",
        "현행 지수와 기준월이 다르므로 장기 비교에는 재지수화 변환을 사용하세요.",
        "901Y094",
        "M",
        _TYPE_INDEX_PROPERTIES,
        _TYPE_INDEX_REGIONS,
    ),
    RealEstateMetricSpec(
        "house_monthly_rent_index_long",
        "주택 월세통합가격지수 (장기·2021.06 기준)",
        "House Monthly Rent Index (2021.06 base)",
        "price",
        "월세",
        "2021.06=100",
        "monthly",
        "context",
        "2015-06부터 2025-03까지 이어지는 구 기준월 월세통합가격지수",
        "현행 지수와 기준월이 다르므로 장기 비교에는 재지수화 변환을 사용하세요.",
        "901Y095",
        "M",
        _TYPE_INDEX_PROPERTIES,
        _TYPE_INDEX_REGIONS,
    ),
    RealEstateMetricSpec(
        "apartment_real_transaction_index",
        "아파트 실거래가격지수",
        "Apartment Real Transaction Price Index",
        "price",
        "매매",
        "2017.11=100",
        "monthly",
        "risk_on",
        "실제 신고된 아파트 매매 계약가격으로 만든 지수",
        "조사 기반 지수보다 변동이 크고 계약일 기준이라 신고 지연에 따라 수정될 수 있습니다.",
        "901Y089",
        "M",
        _ALL_PROPERTY,
        _REAL_DEAL_REGIONS,
    ),
    RealEstateMetricSpec(
        "unsold_housing",
        "미분양 주택",
        "Unsold Housing Units",
        "supply",
        "전체",
        "호",
        "monthly",
        "risk_off",
        "분양 후 계약되지 않고 남은 주택 재고",
        "재고가 늘면 분양시장 수요 둔화 신호일 수 있고 가격보다 먼저 움직이는 편입니다.",
        "901Y074",
        "M",
        _ALL_PROPERTY,
        _UNSOLD_REGIONS,
    ),
    RealEstateMetricSpec(
        "housing_permits",
        "주택건설 인허가실적",
        "Housing Construction Permits",
        "supply",
        "전체",
        "호",
        "monthly",
        "context",
        "주택 착공 이전 단계인 건설 인허가 물량",
        "2~3년 뒤 입주물량으로 이어져 중기 공급을 가늠하는 선행 지표입니다.",
        "901Y105",
        "M",
        _ALL_PROPERTY,
        _PERMIT_REGIONS,
    ),
    RealEstateMetricSpec(
        "land_price_change",
        "지가변동률",
        "Land Price Change Rate",
        "price",
        "매매",
        "%",
        "monthly",
        "risk_on",
        "한국부동산원 지역별 월간 지가변동률",
        "주택가격보다 완만하지만 개발 기대와 토지 수요를 반영합니다.",
        "901Y064",
        "M",
        _ALL_PROPERTY,
        _LAND_REGIONS,
    ),
)


def week_start_of(period: date) -> date:
    """관측 시점이 속한 주의 월요일."""
    return period - timedelta(days=period.weekday())


def _ecos_period(period: date, cycle: str) -> str:
    if cycle == "M":
        return period.strftime("%Y%m")
    if cycle == "Q":
        return f"{period.year}Q{(period.month - 1) // 3 + 1}"
    if cycle == "D":
        return period.strftime("%Y%m%d")
    raise ValueError(f"Unsupported ECOS cycle: {cycle}")


def _parse_period(value: str, cycle: str) -> date:
    if cycle == "M":
        return datetime.strptime(value, "%Y%m").date()
    if cycle == "Q":
        year, quarter = int(value[:4]), int(value[-1])
        return date(year, (quarter - 1) * 3 + 1, 1)
    if cycle == "D":
        return datetime.strptime(value, "%Y%m%d").date()
    raise ValueError(f"Unsupported ECOS cycle: {cycle}")


class EcosRealEstateProvider:
    provider_name = "ecos"

    def __init__(self, api_key: str | None = None, session: Any | None = None, timeout: float = 30.0) -> None:
        self.api_key = api_key or os.getenv("BOK_ECOS_API_KEY") or "sample"
        self.session = session or requests.Session()
        self.timeout = timeout
        self.errors: list[str] = []

    def collect(
        self,
        specs: Iterable[RealEstateMetricSpec],
        start_date: date,
        end_date: date,
        run_id: UUID,
        collected_at: datetime,
    ) -> list[RealEstateObservation]:
        self.errors = []
        rows: list[RealEstateObservation] = []
        for spec in specs:
            for property_code, property_name in spec.property_items:
                for region_code, region_name, region_tier in spec.region_items:
                    item_codes = [code for code in (property_code, region_code) if code]
                    try:
                        payload = self._search(spec, item_codes, start_date, end_date)
                    except RuntimeError as exc:
                        self.errors.append(f"{spec.metric_id}/{region_name}/{property_name}: {exc}")
                        continue
                    for item in payload:
                        raw_value = str(item.get("DATA_VALUE") or "").replace(",", "").strip()
                        if not raw_value or raw_value == "-":
                            continue
                        period = _parse_period(str(item["TIME"]), spec.cycle)
                        rows.append(
                            RealEstateObservation(
                                period=period,
                                week_start=week_start_of(period),
                                metric_id=spec.metric_id,
                                region=region_name,
                                region_tier=region_tier,
                                property_type=property_name,
                                deal_type=spec.deal_type,
                                frequency=spec.frequency,
                                unit=str(item.get("UNIT_NAME") or spec.unit),
                                provider=spec.provider,
                                value=float(raw_value),
                                metadata_json=json.dumps(
                                    {
                                        "stat_code": spec.stat_code,
                                        "item_codes": item_codes,
                                        "stat_name": item.get("STAT_NAME"),
                                    },
                                    ensure_ascii=False,
                                    sort_keys=True,
                                ),
                                run_id=run_id,
                                collected_at=collected_at,
                            )
                        )
        return rows

    def _search(
        self,
        spec: RealEstateMetricSpec,
        item_codes: list[str],
        start_date: date,
        end_date: date,
    ) -> list[dict]:
        page_size = 10 if self.api_key == "sample" else 1000
        start_row, total = 1, 1
        rows: list[dict] = []
        suffix = "/".join(item_codes)
        while start_row <= total:
            end_row = start_row + page_size - 1
            url = (
                f"{ECOS_BASE_URL}/{self.api_key}/json/kr/{start_row}/{end_row}/"
                f"{spec.stat_code}/{spec.cycle}/{_ecos_period(start_date, spec.cycle)}/"
                f"{_ecos_period(end_date, spec.cycle)}/{suffix}"
            )
            try:
                response = self.session.get(url, timeout=self.timeout, headers={"User-Agent": "Cluefin/0.1"})
            except requests.RequestException as exc:
                raise RuntimeError(f"request failed: {type(exc).__name__}") from None
            if getattr(response, "status_code", 200) != 200:
                raise RuntimeError(f"HTTP {response.status_code}")
            payload = response.json()
            if payload.get("RESULT"):
                code = str(payload["RESULT"].get("CODE") or "")
                message = str(payload["RESULT"].get("MESSAGE") or payload["RESULT"])
                # 조회 구간에 값이 없는 정상 응답과 실제 오류를 구분한다.
                if code == "INFO-200" or "해당하는 데이터가 없습니다" in message:
                    return rows
                raise RuntimeError(message)
            result = payload.get("StatisticSearch", {})
            page_rows = result.get("row", [])
            rows.extend(page_rows)
            total = int(result.get("list_total_count") or len(rows))
            if not page_rows:
                break
            start_row += page_size
        return rows


def collect_real_estate(
    store,
    start_date: date,
    end_date: date,
    *,
    metrics: Iterable[str] | None = None,
    provider: Any | None = None,
    strict: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """카탈로그를 ClickHouse에 반영하고 지정한 구간의 관측치를 적재한다."""
    selected = tuple(spec for spec in REAL_ESTATE_CATALOG if metrics is None or spec.metric_id in set(metrics))
    if dry_run:
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "metrics": [spec.metric_id for spec in selected],
            "series": sum(len(spec.property_items) * len(spec.region_items) for spec in selected),
            "dry_run": True,
        }

    run_id = uuid4()
    collected_at = datetime.now()
    store.apply_schema()
    store.insert_records("market.real_estate_metrics", [spec.as_metric(collected_at) for spec in selected])

    collector = provider or EcosRealEstateProvider()
    observations = collector.collect(selected, start_date, end_date, run_id, collected_at)
    errors = list(getattr(collector, "errors", []))
    if errors and strict:
        raise RuntimeError("; ".join(errors))
    if observations:
        store.insert_records("market.real_estate_observations", observations)

    counts: dict[str, int] = {}
    for row in observations:
        counts[row.metric_id] = counts.get(row.metric_id, 0) + 1
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "run_id": str(run_id),
        "metrics": len(selected),
        "observations": len(observations),
        "by_metric": counts,
        "errors": errors,
    }
