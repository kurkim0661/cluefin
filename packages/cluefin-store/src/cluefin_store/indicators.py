from __future__ import annotations

import csv
import gzip
import io
import json
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from statistics import median
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID
from xml.etree import ElementTree
from zipfile import ZipFile

import requests

from cluefin_store.db import ClickHouseStore
from cluefin_store.models import IndicatorDefinition, IndicatorObservation

FRED_GRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
COIN_METRICS_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
DEFILLAMA_STABLECOIN_URL = "https://stablecoins.llama.fi/stablecoincharts/all"
DEFILLAMA_TVL_URL = "https://api.llama.fi/v2/historicalChainTvl"
DEFILLAMA_FEES_URL = "https://api.llama.fi/overview/fees"
COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
COINGECKO_PAXG_URL = "https://api.coingecko.com/api/v3/coins/pax-gold/market_chart"
BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
BINANCE_OPEN_INTEREST_URL = "https://fapi.binance.com/futures/data/openInterestHist"
MONTHLY_LOOKBACK_MONTHS = 4


@dataclass(frozen=True, slots=True)
class IndicatorSpec:
    indicator_id: str
    name_ko: str
    name_en: str
    domain: str
    category: str
    provider: str
    source_series: str
    unit: str
    frequency: str
    higher_is: str
    importance: int
    description_ko: str
    interpretation_ko: str
    source_url: str
    availability: str = "public"
    scale: float = 1.0

    def definition(self, updated_at: datetime) -> IndicatorDefinition:
        return IndicatorDefinition(
            indicator_id=self.indicator_id,
            name_ko=self.name_ko,
            name_en=self.name_en,
            domain=self.domain,
            category=self.category,
            provider=self.provider,
            source_series=self.source_series,
            unit=self.unit,
            frequency=self.frequency,
            higher_is=self.higher_is,
            importance=self.importance,
            description_ko=self.description_ko,
            interpretation_ko=self.interpretation_ko,
            source_url=self.source_url,
            availability=self.availability,
            updated_at=updated_at,
        )


def _fred(
    indicator_id: str,
    name_ko: str,
    name_en: str,
    series: str,
    category: str,
    unit: str,
    frequency: str,
    higher_is: str,
    interpretation: str,
    *,
    domain: str = "global",
    importance: int = 2,
    scale: float = 1.0,
) -> IndicatorSpec:
    return IndicatorSpec(
        indicator_id=indicator_id,
        name_ko=name_ko,
        name_en=name_en,
        domain=domain,
        category=category,
        provider="fred",
        source_series=series,
        unit=unit,
        frequency=frequency,
        higher_is=higher_is,
        importance=importance,
        description_ko=f"미 연준 FRED의 {series} 시계열",
        interpretation_ko=interpretation,
        source_url=f"https://fred.stlouisfed.org/series/{series}",
        scale=scale,
    )


def _ecos_fx(
    indicator_id: str,
    name_ko: str,
    name_en: str,
    item_code: str,
    unit: str,
    description: str,
    interpretation: str,
    *,
    higher_is: str = "context",
    importance: int = 2,
) -> IndicatorSpec:
    """한국은행 일별 환율(ECOS 731Y001).

    FRED의 원/달러(DEXKOUS)는 주 단위로 뒤늦게 올라와 오늘 값이 비어 있는 날이 대부분이다.
    ECOS 일별 시리즈는 당일 매매기준율을 담고 있어 환율을 매일 채우려면 이쪽을 써야 한다.
    """
    return IndicatorSpec(
        indicator_id=indicator_id,
        name_ko=name_ko,
        name_en=name_en,
        domain="korea",
        category="fx",
        provider="ecos",
        source_series=f"731Y001:D:{item_code}",
        unit=unit,
        frequency="daily",
        higher_is=higher_is,
        importance=importance,
        description_ko=description,
        interpretation_ko=interpretation,
        source_url="https://ecos.bok.or.kr/",
    )


INDICATOR_CATALOG: tuple[IndicatorSpec, ...] = (
    _fred(
        "us_fed_funds",
        "미국 정책금리",
        "Effective Federal Funds Rate",
        "DFF",
        "rates",
        "%",
        "daily",
        "risk_off",
        "상승은 금융여건을 조이고 위험자산 할인율을 높입니다.",
        importance=3,
    ),
    _fred(
        "us_treasury_2y",
        "미국 2년물 금리",
        "US Treasury 2Y",
        "DGS2",
        "rates",
        "%",
        "daily",
        "risk_off",
        "통화정책 기대에 민감한 단기 금리입니다.",
        importance=3,
    ),
    _fred(
        "us_treasury_10y",
        "미국 10년물 금리",
        "US Treasury 10Y",
        "DGS10",
        "rates",
        "%",
        "daily",
        "risk_off",
        "장기 할인율과 성장·물가 기대를 함께 반영합니다.",
        importance=3,
    ),
    _fred(
        "us_real_yield_10y",
        "미국 10년 실질금리",
        "US 10Y Real Yield",
        "DFII10",
        "rates",
        "%",
        "daily",
        "risk_off",
        "상승은 성장주와 코인처럼 듀레이션이 긴 자산에 부담입니다.",
        importance=3,
    ),
    _fred(
        "us_curve_10y_3m",
        "미국 10년-3개월 금리차",
        "US 10Y-3M Spread",
        "T10Y3M",
        "rates",
        "%p",
        "daily",
        "context",
        "역전과 이후 재가팔라짐을 경기침체 위험과 함께 봅니다.",
        importance=3,
    ),
    _fred(
        "us_curve_10y_2y",
        "미국 10년-2년 금리차",
        "US 10Y-2Y Spread",
        "T10Y2Y",
        "rates",
        "%p",
        "daily",
        "context",
        "정책과 장기 성장 기대의 상대적인 기울기입니다.",
    ),
    _fred(
        "us_breakeven_10y",
        "미국 10년 기대인플레이션",
        "US 10Y Breakeven Inflation",
        "T10YIE",
        "inflation",
        "%",
        "daily",
        "risk_off",
        "빠른 상승은 장기금리와 긴축 기대를 자극할 수 있습니다.",
    ),
    _fred(
        "us_cpi",
        "미국 소비자물가",
        "US CPI",
        "CPIAUCSL",
        "inflation",
        "index",
        "monthly",
        "risk_off",
        "시장 예상 대비 상승률과 3개월 추세가 중요합니다.",
        importance=3,
    ),
    _fred(
        "us_core_cpi",
        "미국 근원 소비자물가",
        "US Core CPI",
        "CPILFESL",
        "inflation",
        "index",
        "monthly",
        "risk_off",
        "서비스 물가의 지속성과 금리 경로 판단에 사용합니다.",
        importance=3,
    ),
    _fred(
        "us_core_pce",
        "미국 근원 PCE",
        "US Core PCE",
        "PCEPILFE",
        "inflation",
        "index",
        "monthly",
        "risk_off",
        "연준이 중시하는 기조 물가 지표입니다.",
        importance=3,
    ),
    _fred(
        "us_ppi",
        "미국 생산자물가",
        "US Producer Price Index",
        "PPIACO",
        "inflation",
        "index",
        "monthly",
        "risk_off",
        "기업 투입비용과 소비자물가 압력을 보완합니다.",
    ),
    _fred(
        "us_real_gdp",
        "미국 실질 GDP",
        "US Real GDP",
        "GDPC1",
        "growth",
        "USD bn",
        "quarterly",
        "risk_on",
        "경제의 사후 확인 지표로 선행지표와 함께 봅니다.",
        importance=3,
    ),
    _fred(
        "us_industrial_production",
        "미국 산업생산",
        "US Industrial Production",
        "INDPRO",
        "growth",
        "index",
        "monthly",
        "risk_on",
        "제조업과 경기순환 업종의 실제 활동을 보여줍니다.",
    ),
    _fred(
        "us_retail_sales",
        "미국 소매판매",
        "US Retail Sales",
        "RSAFS",
        "growth",
        "USD mn",
        "monthly",
        "risk_on",
        "미국 소비의 명목 지출 모멘텀을 보여줍니다.",
    ),
    _fred(
        "us_unemployment",
        "미국 실업률",
        "US Unemployment Rate",
        "UNRATE",
        "labor",
        "%",
        "monthly",
        "risk_off",
        "급격한 상승은 경기침체와 이익 감소 위험을 높입니다.",
        importance=3,
    ),
    _fred(
        "us_nonfarm_payrolls",
        "미국 비농업고용",
        "US Nonfarm Payrolls",
        "PAYEMS",
        "labor",
        "thousand",
        "monthly",
        "risk_on",
        "고용 증가와 전월 수정, 시장 예상치를 함께 봅니다.",
        importance=3,
    ),
    _fred(
        "us_average_hourly_earnings",
        "미국 시간당 평균임금",
        "US Average Hourly Earnings",
        "CES0500000003",
        "labor",
        "USD",
        "monthly",
        "context",
        "임금 상승은 소비를 지지하지만 서비스 물가를 자극할 수 있습니다.",
    ),
    _fred(
        "us_initial_claims",
        "미국 신규 실업수당",
        "US Initial Jobless Claims",
        "ICSA",
        "labor",
        "count",
        "weekly",
        "risk_off",
        "고용 둔화를 비교적 빠르게 포착하는 주간 지표입니다.",
        importance=3,
    ),
    _fred(
        "broad_usd",
        "미국 광의 달러지수",
        "Nominal Broad US Dollar Index",
        "DTWEXBGS",
        "fx",
        "index",
        "daily",
        "risk_off",
        "강달러는 글로벌 유동성과 신흥시장 금융여건을 압박합니다.",
        importance=3,
    ),
    _fred(
        "us_high_yield_oas",
        "미국 하이일드 스프레드",
        "US High Yield OAS",
        "BAMLH0A0HYM2",
        "credit",
        "%p",
        "daily",
        "risk_off",
        "급등은 신용위험과 위험회피 확대를 뜻합니다.",
        importance=3,
    ),
    _fred(
        "us_nfci",
        "미국 금융여건지수",
        "Chicago Fed NFCI",
        "NFCI",
        "credit",
        "index",
        "weekly",
        "risk_off",
        "0보다 높으면 장기 평균보다 긴축적인 금융여건입니다.",
        importance=3,
    ),
    _fred(
        "fed_total_assets",
        "연준 총자산",
        "Federal Reserve Total Assets",
        "WALCL",
        "liquidity",
        "USD bn",
        "weekly",
        "risk_on",
        "증가는 금융시스템 유동성 공급을 나타내는 한 축입니다.",
        importance=3,
        scale=0.001,
    ),
    _fred(
        "fed_reserve_balances",
        "은행 지급준비금",
        "Reserve Balances at Federal Reserve",
        "WRESBAL",
        "liquidity",
        "USD bn",
        "weekly",
        "risk_on",
        "은행 시스템이 실제로 보유한 준비금 수준입니다.",
        scale=0.001,
    ),
    _fred(
        "us_treasury_tga",
        "미 재무부 TGA",
        "US Treasury General Account",
        "WTREGEN",
        "liquidity",
        "USD bn",
        "weekly",
        "risk_off",
        "잔액 증가는 민간 유동성을 흡수하는 방향으로 작용할 수 있습니다.",
        importance=3,
        scale=0.001,
    ),
    _fred(
        "fed_overnight_rrp",
        "연준 역레포",
        "Fed Overnight Reverse Repo",
        "RRPONTSYD",
        "liquidity",
        "USD bn",
        "daily",
        "risk_off",
        "잔액 감소는 다른 조건이 같다면 시장 유동성을 방출할 수 있습니다.",
    ),
    _fred(
        "us_m2",
        "미국 M2",
        "US M2 Money Stock",
        "M2SL",
        "liquidity",
        "USD bn",
        "monthly",
        "risk_on",
        "광의 통화량의 중기 추세를 보여주지만 시차가 큽니다.",
    ),
    _fred(
        "sp500",
        "S&P 500",
        "S&P 500",
        "SP500",
        "market",
        "index",
        "daily",
        "context",
        "거시 지표와 비교할 위험자산 기준선입니다.",
    ),
    _fred(
        "vix",
        "VIX 변동성지수",
        "CBOE VIX",
        "VIXCLS",
        "positioning",
        "index",
        "daily",
        "risk_off",
        "펀더멘털이 아니라 단기 위험회피와 옵션 가격 지표입니다.",
    ),
    _fred(
        "wti",
        "WTI 유가",
        "WTI Crude Oil",
        "DCOILWTICO",
        "commodities",
        "USD",
        "daily",
        "context",
        "인플레이션과 에너지 업종 이익에 동시에 영향을 줍니다.",
    ),
    _fred(
        "copper",
        "구리 가격",
        "Global Copper Price",
        "PCOPPUSDM",
        "commodities",
        "USD/mt",
        "monthly",
        "risk_on",
        "글로벌 제조업과 중국 수요의 대용지표로 활용됩니다.",
    ),
    IndicatorSpec(
        "gold",
        "금 가격 대용치",
        "Gold Price Proxy",
        "global",
        "commodities",
        "coingecko",
        "coin.pax-gold.usd",
        "USD/oz",
        "daily",
        "context",
        2,
        "금 1트로이온스 연동 토큰 PAXG의 달러 가격",
        "실질금리·달러·안전자산 수요를 반영하지만 현물 금과 소폭 괴리가 생길 수 있습니다.",
        "https://www.coingecko.com/en/coins/pax-gold",
    ),
    IndicatorSpec(
        "fed_net_liquidity",
        "연준 순유동성 대용치",
        "Fed Net Liquidity Proxy",
        "global",
        "liquidity",
        "derived",
        "WALCL-WTREGEN-RRPONTSYD",
        "USD bn",
        "daily",
        "risk_on",
        3,
        "연준 총자산에서 TGA와 역레포를 뺀 비공식 대용치",
        "공식 지수가 아니므로 방향성과 구성 항목을 함께 확인합니다.",
        "https://fred.stlouisfed.org/",
        "derived",
    ),
    IndicatorSpec(
        "stablecoin_supply",
        "스테이블코인 공급",
        "Stablecoin Circulating Supply",
        "crypto",
        "crypto_liquidity",
        "defillama",
        "stablecoincharts/all",
        "USD",
        "daily",
        "risk_on",
        3,
        "달러 연동 스테이블코인의 총 유통가치",
        "증가는 코인시장에 투입 가능한 구매력 확대 신호가 될 수 있습니다.",
        "https://defillama.com/stablecoins",
    ),
    IndicatorSpec(
        "defi_tvl",
        "DeFi 총 예치자산",
        "DeFi Total Value Locked",
        "crypto",
        "protocol",
        "defillama",
        "historicalChainTvl",
        "USD",
        "daily",
        "risk_on",
        2,
        "DeFi 프로토콜 전체의 추정 TVL",
        "가격 상승에 의해 함께 증가할 수 있어 사용자·수익 지표와 병행합니다.",
        "https://defillama.com/",
    ),
    IndicatorSpec(
        "defi_fees",
        "DeFi 일간 수수료",
        "DeFi Daily Fees",
        "crypto",
        "protocol",
        "defillama",
        "dailyFees",
        "USD",
        "daily",
        "risk_on",
        2,
        "DeFi 프로토콜에서 발생한 일간 총 수수료",
        "TVL보다 실제 사용과 지불 의사를 직접 보여주는 지표입니다.",
        "https://defillama.com/fees",
    ),
    IndicatorSpec(
        "defi_revenue",
        "DeFi 일간 프로토콜 수익",
        "DeFi Daily Revenue",
        "crypto",
        "protocol",
        "defillama",
        "dailyRevenue",
        "USD",
        "daily",
        "risk_on",
        2,
        "토큰 보유자 또는 프로토콜에 귀속되는 일간 수익",
        "수수료와 수익의 차이를 통해 실제 가치 포착 정도를 봅니다.",
        "https://defillama.com/fees",
    ),
    IndicatorSpec(
        "crypto_total_market_cap",
        "코인 전체 시가총액",
        "Crypto Total Market Cap",
        "crypto",
        "market",
        "coingecko",
        "total_market_cap.usd",
        "USD",
        "daily",
        "context",
        2,
        "CoinGecko가 집계한 전체 코인 시가총액",
        "시장 규모의 현재 스냅샷입니다.",
        "https://www.coingecko.com/",
    ),
    IndicatorSpec(
        "crypto_total_volume",
        "코인 24시간 거래대금",
        "Crypto 24H Volume",
        "crypto",
        "market",
        "coingecko",
        "total_volume.usd",
        "USD",
        "daily",
        "context",
        2,
        "CoinGecko 집계 24시간 거래대금",
        "급증은 관심 확대 또는 투매를 모두 의미할 수 있습니다.",
        "https://www.coingecko.com/",
    ),
    IndicatorSpec(
        "btc_dominance",
        "비트코인 도미넌스",
        "Bitcoin Dominance",
        "crypto",
        "positioning",
        "coingecko",
        "market_cap_percentage.btc",
        "%",
        "daily",
        "context",
        2,
        "전체 코인 시가총액 중 비트코인 비중",
        "상승은 비트코인 선호 또는 알트코인 위험회피를 뜻할 수 있습니다.",
        "https://www.coingecko.com/",
    ),
    IndicatorSpec(
        "crypto_market_cap_change_24h",
        "코인 시총 24시간 변화",
        "Crypto Market Cap Change 24H",
        "crypto",
        "positioning",
        "coingecko",
        "market_cap_change_percentage_24h_usd",
        "%",
        "daily",
        "context",
        1,
        "전체 코인 시가총액의 24시간 변화율",
        "단기 시장 온도를 보여주는 포지셔닝 지표입니다.",
        "https://www.coingecko.com/",
    ),
    IndicatorSpec(
        "btc_mvrv",
        "비트코인 MVRV",
        "Bitcoin MVRV",
        "crypto",
        "onchain",
        "coinmetrics",
        "btc:CapMVRVCur",
        "ratio",
        "daily",
        "context",
        3,
        "시장가치와 실현가치의 비율",
        "높을수록 미실현 이익과 잠재 매도 압력이 커질 수 있습니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "btc_price_usd",
        "비트코인 기준가격",
        "Bitcoin Price USD",
        "crypto",
        "market",
        "coinmetrics",
        "btc:PriceUSD",
        "USD",
        "daily",
        "context",
        2,
        "Coin Metrics의 비트코인 달러 기준가격",
        "온체인 가치평가 지표의 기준 가격으로 사용합니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "btc_realized_price",
        "비트코인 실현가격",
        "Bitcoin Realized Price",
        "crypto",
        "onchain",
        "derived",
        "PriceUSD/CapMVRVCur",
        "USD",
        "daily",
        "context",
        3,
        "현재 가격을 MVRV로 나눈 전체 공급의 추정 취득원가",
        "가격이 실현가격 아래로 내려가면 시장 전체 손실 구간을 뜻할 수 있습니다.",
        "https://docs.coinmetrics.io/",
        "derived",
    ),
    IndicatorSpec(
        "btc_active_addresses",
        "비트코인 활성 주소",
        "Bitcoin Active Addresses",
        "crypto",
        "onchain",
        "coinmetrics",
        "btc:AdrActCnt",
        "count",
        "daily",
        "risk_on",
        2,
        "하루 동안 활동한 비트코인 주소 수",
        "주소 중복과 거래소 내부 이동을 감안해 추세로 봅니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "btc_transactions",
        "비트코인 거래 수",
        "Bitcoin Transactions",
        "crypto",
        "onchain",
        "coinmetrics",
        "btc:TxCnt",
        "count",
        "daily",
        "risk_on",
        2,
        "비트코인 네트워크의 일간 거래 수",
        "네트워크 사용 활동의 기본 지표입니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "btc_hashrate",
        "비트코인 해시레이트",
        "Bitcoin Hash Rate",
        "crypto",
        "network",
        "coinmetrics",
        "btc:HashRate",
        "TH/s",
        "daily",
        "risk_on",
        2,
        "비트코인 네트워크의 추정 연산력",
        "장기 상승은 네트워크 보안과 채굴 투자 확대를 뜻합니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "btc_supply",
        "비트코인 유통량",
        "Bitcoin Current Supply",
        "crypto",
        "supply",
        "coinmetrics",
        "btc:SplyCur",
        "BTC",
        "daily",
        "context",
        1,
        "현재 발행된 비트코인 공급량",
        "발행 속도와 반감기 효과를 추적합니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "eth_mvrv",
        "이더리움 MVRV",
        "Ethereum MVRV",
        "crypto",
        "onchain",
        "coinmetrics",
        "eth:CapMVRVCur",
        "ratio",
        "daily",
        "context",
        2,
        "이더리움 시장가치와 실현가치의 비율",
        "보유자의 미실현 손익 극단을 판단하는 보조지표입니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "eth_price_usd",
        "이더리움 기준가격",
        "Ethereum Price USD",
        "crypto",
        "market",
        "coinmetrics",
        "eth:PriceUSD",
        "USD",
        "daily",
        "context",
        2,
        "Coin Metrics의 이더리움 달러 기준가격",
        "온체인 활동과 가격 효과를 분리할 때 사용합니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "eth_active_addresses",
        "이더리움 활성 주소",
        "Ethereum Active Addresses",
        "crypto",
        "onchain",
        "coinmetrics",
        "eth:AdrActCnt",
        "count",
        "daily",
        "risk_on",
        2,
        "하루 동안 활동한 이더리움 주소 수",
        "봇과 L2 이동을 고려해 추세로 판단합니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "eth_transactions",
        "이더리움 거래 수",
        "Ethereum Transactions",
        "crypto",
        "onchain",
        "coinmetrics",
        "eth:TxCnt",
        "count",
        "daily",
        "risk_on",
        2,
        "이더리움 메인넷의 일간 거래 수",
        "L2 확장으로 메인넷 거래만으로 전체 수요를 설명할 수 없습니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "eth_supply",
        "이더리움 유통량",
        "Ethereum Current Supply",
        "crypto",
        "supply",
        "coinmetrics",
        "eth:SplyCur",
        "ETH",
        "daily",
        "context",
        1,
        "현재 이더리움 공급량",
        "발행·소각·스테이킹을 함께 판단해야 합니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "kr_policy_rate",
        "한국은행 기준금리",
        "Bank of Korea Base Rate",
        "korea",
        "rates",
        "ecos",
        "722Y001:D:0101000",
        "%",
        "daily",
        "risk_off",
        3,
        "한국은행 기준금리 일별 적용 수준",
        "월간 시리즈는 한 달 이상 지연되므로 발표 당일 반영되는 일별 시리즈를 사용합니다.",
        "https://ecos.bok.or.kr/",
    ),
    _ecos_fx(
        "usd_krw",
        "원·달러 환율",
        "KRW per USD",
        "0000001",
        "KRW",
        "한국은행이 매 영업일 고시하는 원/미국달러 매매기준율",
        "상승은 원화 약세로 외국인 수급과 수입물가 부담을 키울 수 있습니다.",
        higher_is="risk_off",
        importance=3,
    ),
    _ecos_fx(
        "jpy_krw",
        "원·엔 환율",
        "KRW per 100 JPY",
        "0000002",
        "KRW/100JPY",
        "한국은행 일별 원/일본엔(100엔) 환율",
        "엔화 강세는 위험회피 국면에서 함께 나타나며, 한국 수출기업의 가격 경쟁력에도 영향을 줍니다.",
    ),
    _ecos_fx(
        "eur_krw",
        "원·유로 환율",
        "KRW per EUR",
        "0000003",
        "KRW",
        "한국은행 일별 원/유로 환율",
        "달러 대비 유로 강약과 함께 보면 원화 약세가 달러 요인인지 원화 요인인지 가늠할 수 있습니다.",
    ),
    _ecos_fx(
        "cny_krw",
        "원·위안 환율",
        "KRW per CNY",
        "0000053",
        "KRW",
        "한국은행 일별 원/위안 매매기준율",
        "위안화와 원화는 같은 방향으로 움직이는 경향이 있어 중국 리스크를 함께 확인합니다.",
    ),
    IndicatorSpec(
        "kr_exports_20d",
        "한국 1~20일 수출",
        "Korea First 20 Days Exports",
        "korea",
        "growth",
        "customs",
        "exports_20d",
        "% YoY",
        "monthly",
        "risk_on",
        3,
        "관세청 1~20일 수출 전년동기대비 증가율",
        "조업일수 차이와 일평균 수출액을 함께 확인합니다.",
        "https://www.customs.go.kr/kcs/na/ntt/selectNttList.do?mi=2891&bbsId=1362",
    ),
    IndicatorSpec(
        "kr_export_value_index_yoy",
        "한국 수출금액지수 증가율",
        "Korea Export Value Index YoY",
        "korea",
        "growth",
        "ecos",
        "403Y001:M:*AA:yoy",
        "% YoY",
        "monthly",
        "risk_on",
        3,
        "한국은행 수출금액 총지수의 전년 대비 증가율",
        "한국 수출 경기의 월간 방향을 확인합니다.",
        "https://ecos.bok.or.kr/",
    ),
    IndicatorSpec(
        "kr_semiconductor_exports",
        "한국 반도체 수출금액지수 증가율",
        "Korea Semiconductor Export Value Index YoY",
        "korea",
        "growth",
        "ecos",
        "403Y001:M:30911AA:yoy",
        "% YoY",
        "monthly",
        "risk_on",
        3,
        "한국은행 반도체 수출금액지수의 전년 대비 증가율",
        "한국 증시 이익 사이클의 핵심 선행지표입니다.",
        "https://ecos.bok.or.kr/",
    ),
    IndicatorSpec(
        "kr_revenue_growth_breadth",
        "한국 상장사 매출 성장 폭",
        "Korea Revenue Growth Breadth",
        "equity",
        "earnings",
        "dart",
        "REVENUE_GROWTH_BREADTH",
        "%",
        "annual",
        "risk_on",
        3,
        "전년 대비 매출이 증가한 유니버스 종목 비율",
        "DART 재무제표를 유니버스 단위로 집계합니다.",
        "https://opendart.fss.or.kr/",
        "api_key",
    ),
    IndicatorSpec(
        "kr_operating_profit_growth_breadth",
        "한국 상장사 영업이익 성장 폭",
        "Korea Operating Profit Growth Breadth",
        "equity",
        "earnings",
        "dart",
        "OPERATING_PROFIT_GROWTH_BREADTH",
        "%",
        "annual",
        "risk_on",
        3,
        "전년 대비 영업이익이 증가한 유니버스 종목 비율",
        "지수 상승이 실제 이익 개선과 함께 가는지 확인합니다.",
        "https://opendart.fss.or.kr/",
        "api_key",
    ),
    IndicatorSpec(
        "kr_roe_median",
        "한국 상장사 ROE 중앙값",
        "Korea Median ROE",
        "equity",
        "earnings",
        "dart",
        "ROE_MEDIAN",
        "%",
        "annual",
        "risk_on",
        2,
        "유니버스 기업 ROE의 중앙값",
        "극단값 영향을 줄여 기업 수익성의 전반적인 수준을 봅니다.",
        "https://opendart.fss.or.kr/",
        "api_key",
    ),
    IndicatorSpec(
        "kr_debt_ratio_median",
        "한국 상장사 부채비율 중앙값",
        "Korea Median Debt Ratio",
        "equity",
        "credit",
        "dart",
        "DEBT_RATIO_MEDIAN",
        "%",
        "annual",
        "risk_off",
        2,
        "유니버스 기업 부채비율의 중앙값",
        "금리 상승기에 재무 취약성이 커지는지 확인합니다.",
        "https://opendart.fss.or.kr/",
        "api_key",
    ),
    _fred(
        "us_ism_new_orders",
        "미국 제조업 신규주문",
        "US Manufacturers New Orders",
        "AMTMNO",
        "growth",
        "USD mn",
        "monthly",
        "risk_on",
        "ISM 유료지표 대신 미국 Census의 실제 제조업 신규주문 금액을 사용합니다.",
        importance=3,
    ),
    IndicatorSpec(
        "equity_eps_revisions",
        "주식 EPS 수정비율",
        "Equity EPS Revision Breadth",
        "equity",
        "earnings",
        "licensed",
        "EPS_REVISION_BREADTH",
        "%",
        "daily",
        "risk_on",
        3,
        "상향과 하향 추정치 수정의 시장 폭",
        "FactSet·Bloomberg·FnGuide 등 계약 데이터가 필요합니다.",
        "",
        "licensed",
    ),
    IndicatorSpec(
        "btc_spot_etf_flow",
        "비트코인 현물 ETF 순유입",
        "Bitcoin Spot ETF Net Flow",
        "crypto",
        "flows",
        "licensed",
        "BTC_SPOT_ETF_NET_FLOW",
        "USD",
        "daily",
        "risk_on",
        3,
        "미국 현물 ETF의 일간 순설정액",
        "공급자별 집계 기준을 통일한 계약 데이터가 필요합니다.",
        "",
        "licensed",
    ),
    IndicatorSpec(
        "crypto_exchange_netflow",
        "거래소 코인 순유입",
        "Crypto Exchange Netflow",
        "crypto",
        "flows",
        "licensed",
        "EXCHANGE_NETFLOW",
        "USD",
        "daily",
        "risk_off",
        3,
        "추적 거래소 지갑의 순입출금",
        "CryptoQuant·Glassnode 등 라벨링 데이터 공급자가 필요합니다.",
        "",
        "licensed",
    ),
    IndicatorSpec(
        "token_unlock_value",
        "토큰 언락 예정액",
        "Scheduled Token Unlock Value",
        "crypto",
        "supply",
        "licensed",
        "TOKEN_UNLOCK_VALUE",
        "USD",
        "daily",
        "risk_off",
        2,
        "예정된 토큰 락업 해제 가치",
        "Tokenomist 등 일정 공급자 계약이 필요합니다.",
        "",
        "licensed",
    ),
    IndicatorSpec(
        "btc_sopr",
        "비트코인 SOPR",
        "Bitcoin SOPR",
        "crypto",
        "onchain",
        "licensed",
        "BTC_SOPR",
        "ratio",
        "daily",
        "context",
        3,
        "사용된 코인의 실현 손익 비율",
        "1 위와 아래의 움직임으로 이익실현과 손절 우위를 판단합니다.",
        "",
        "licensed",
    ),
    IndicatorSpec(
        "btc_nupl",
        "비트코인 NUPL",
        "Bitcoin NUPL",
        "crypto",
        "onchain",
        "derived",
        "1-1/CapMVRVCur",
        "ratio",
        "daily",
        "context",
        2,
        "MVRV로부터 계산한 전체 공급의 순미실현 손익 비율",
        "시장 참여자의 이익과 손실 극단을 보여줍니다.",
        "https://docs.coinmetrics.io/",
        "derived",
    ),
    IndicatorSpec(
        "btc_lth_supply",
        "비트코인 장기보유자 공급",
        "Bitcoin Long-Term Holder Supply",
        "crypto",
        "supply",
        "licensed",
        "BTC_LTH_SUPPLY",
        "BTC",
        "daily",
        "risk_on",
        2,
        "장기간 이동하지 않은 비트코인 공급량",
        "증가는 장기 보유자의 축적을 뜻할 수 있습니다.",
        "",
        "licensed",
    ),
    IndicatorSpec(
        "crypto_funding_rate",
        "비트코인 무기한선물 펀딩비",
        "Bitcoin Perpetual Funding Rate",
        "crypto",
        "positioning",
        "binance",
        "BTCUSDT:fundingRate",
        "%",
        "daily",
        "context",
        2,
        "Binance BTCUSDT 무기한선물의 일평균 펀딩비",
        "펀더멘털이 아니라 레버리지 쏠림을 확인하는 지표입니다.",
        "https://www.binance.com/en/futures/funding-history/perpetual/real-time-funding-rate",
    ),
    IndicatorSpec(
        "crypto_open_interest",
        "비트코인 선물 미결제약정",
        "Bitcoin Futures Open Interest",
        "crypto",
        "positioning",
        "binance",
        "BTCUSDT:openInterest",
        "USD",
        "daily",
        "risk_off",
        2,
        "Binance BTCUSDT 선물 미결제약정 달러 가치",
        "가격과 함께 급증하면 청산 위험이 커질 수 있습니다.",
        "https://www.binance.com/en/futures/BTCUSDT",
    ),
    IndicatorSpec(
        "crypto_liquidations",
        "코인 선물 청산액",
        "Crypto Futures Liquidations",
        "crypto",
        "positioning",
        "licensed",
        "FUTURES_LIQUIDATIONS",
        "USD",
        "hourly",
        "risk_off",
        1,
        "주요 거래소 선물 강제청산액",
        "방향성 펀더멘털이 아닌 스트레스와 포지셔닝 지표입니다.",
        "",
        "licensed",
    ),
    IndicatorSpec(
        "protocol_active_users",
        "프로토콜 활성 사용자",
        "Protocol Active Users",
        "crypto",
        "protocol",
        "licensed",
        "ACTIVE_USERS",
        "count",
        "daily",
        "risk_on",
        2,
        "선택 프로토콜의 중복 제거 활성 사용자",
        "인센티브와 봇을 제거한 공급자 데이터가 필요합니다.",
        "",
        "licensed",
    ),
    IndicatorSpec(
        "protocol_token_inflation",
        "이더리움 30일 공급 증가율",
        "Ethereum 30D Supply Growth",
        "crypto",
        "supply",
        "derived",
        "eth:SplyCur:30d_change",
        "%",
        "daily",
        "risk_off",
        2,
        "Coin Metrics 이더리움 공급량으로 계산한 30일 순증가율 대용치",
        "양수면 공급 희석, 음수면 순소각 방향으로 해석합니다.",
        "https://docs.coinmetrics.io/",
        "derived",
    ),
    IndicatorSpec(
        "eth_realized_price",
        "이더리움 실현가격",
        "Ethereum Realized Price",
        "crypto",
        "onchain",
        "derived",
        "eth:PriceUSD/CapMVRVCur",
        "USD",
        "daily",
        "context",
        2,
        "이더리움 가격을 MVRV로 나눈 전체 공급의 추정 취득원가",
        "가격이 실현가격 아래로 내려가면 시장 전체 손실 구간을 뜻할 수 있습니다.",
        "https://docs.coinmetrics.io/",
        "derived",
    ),
    IndicatorSpec(
        "eth_nupl",
        "이더리움 NUPL",
        "Ethereum NUPL",
        "crypto",
        "onchain",
        "derived",
        "eth:1-1/CapMVRVCur",
        "ratio",
        "daily",
        "context",
        2,
        "이더리움 MVRV로 계산한 전체 공급의 순미실현 손익 비율",
        "0 아래는 시장 전체 손실 우위, 높은 값은 차익실현 압력 확대를 뜻할 수 있습니다.",
        "https://docs.coinmetrics.io/",
        "derived",
    ),
    IndicatorSpec(
        "eth_active_addresses_ratio",
        "이더리움 거래당 활성 주소",
        "Ethereum Active Addresses per Transaction",
        "crypto",
        "onchain",
        "derived",
        "eth:AdrActCnt/TxCnt",
        "ratio",
        "daily",
        "context",
        1,
        "활성 주소를 거래 수로 나눈 사용 밀도 대용치",
        "값이 낮아지면 소수 주소가 거래를 반복하는 봇·거래소 활동일 수 있습니다.",
        "https://docs.coinmetrics.io/",
        "derived",
    ),
    IndicatorSpec(
        "eth_dominance",
        "이더리움 도미넌스",
        "Ethereum Dominance",
        "crypto",
        "positioning",
        "coingecko",
        "market_cap_percentage.eth",
        "%",
        "daily",
        "context",
        2,
        "전체 코인 시가총액 중 이더리움 비중",
        "상승은 비트코인 대비 알트코인 선호가 살아나는 신호일 수 있습니다.",
        "https://www.coingecko.com/",
    ),
    IndicatorSpec(
        "eth_funding_rate",
        "이더리움 무기한선물 펀딩비",
        "Ethereum Perpetual Funding Rate",
        "crypto",
        "positioning",
        "binance",
        "ETHUSDT:fundingRate",
        "%",
        "daily",
        "context",
        2,
        "Binance ETHUSDT 무기한선물의 일평균 펀딩비",
        "펀더멘털이 아니라 레버리지 쏠림을 확인하는 지표입니다.",
        "https://www.binance.com/en/futures/ETHUSDT",
    ),
    IndicatorSpec(
        "eth_open_interest",
        "이더리움 선물 미결제약정",
        "Ethereum Futures Open Interest",
        "crypto",
        "positioning",
        "binance",
        "ETHUSDT:openInterest",
        "USD",
        "daily",
        "risk_off",
        2,
        "Binance ETHUSDT 선물 미결제약정 달러 가치",
        "가격과 함께 급증하면 청산 위험이 커질 수 있습니다.",
        "https://www.binance.com/en/futures/ETHUSDT",
    ),
    IndicatorSpec(
        "xrp_price_usd",
        "리플 기준가격",
        "XRP Price USD",
        "crypto",
        "market",
        "coinmetrics",
        "xrp:PriceUSD",
        "USD",
        "daily",
        "context",
        2,
        "Coin Metrics의 리플 달러 기준가격",
        "온체인 활동과 가격 효과를 분리할 때 기준으로 사용합니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "xrp_mvrv",
        "리플 MVRV",
        "XRP MVRV",
        "crypto",
        "onchain",
        "coinmetrics",
        "xrp:CapMVRVCur",
        "ratio",
        "daily",
        "context",
        2,
        "리플 시장가치와 실현가치의 비율",
        "1 아래면 평균 취득원가보다 낮은 가격이라는 뜻입니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "xrp_realized_price",
        "리플 실현가격",
        "XRP Realized Price",
        "crypto",
        "onchain",
        "derived",
        "xrp:PriceUSD/CapMVRVCur",
        "USD",
        "daily",
        "context",
        2,
        "리플 가격을 MVRV로 나눈 전체 공급의 추정 취득원가",
        "가격이 실현가격 아래면 보유자 다수가 손실 구간일 수 있습니다.",
        "https://docs.coinmetrics.io/",
        "derived",
    ),
    IndicatorSpec(
        "xrp_nupl",
        "리플 NUPL",
        "XRP NUPL",
        "crypto",
        "onchain",
        "derived",
        "xrp:1-1/CapMVRVCur",
        "ratio",
        "daily",
        "context",
        1,
        "리플 MVRV로 계산한 전체 공급의 순미실현 손익 비율",
        "0 아래는 시장 전체 손실 우위를 뜻할 수 있습니다.",
        "https://docs.coinmetrics.io/",
        "derived",
    ),
    IndicatorSpec(
        "xrp_active_addresses",
        "리플 활성 주소",
        "XRP Active Addresses",
        "crypto",
        "onchain",
        "coinmetrics",
        "xrp:AdrActCnt",
        "count",
        "daily",
        "risk_on",
        2,
        "하루 동안 활동한 리플 주소 수",
        "거래소 내부 이동 비중이 커서 추세로만 봅니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "xrp_transactions",
        "리플 거래 수",
        "XRP Transactions",
        "crypto",
        "onchain",
        "coinmetrics",
        "xrp:TxCnt",
        "count",
        "daily",
        "risk_on",
        2,
        "리플 원장의 일간 거래 수",
        "결제·환전 사용량을 보는 기본 지표입니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "xrp_supply",
        "리플 유통량",
        "XRP Current Supply",
        "crypto",
        "supply",
        "coinmetrics",
        "xrp:SplyCur",
        "XRP",
        "daily",
        "context",
        1,
        "현재 유통 중인 리플 공급량",
        "에스크로 해제 일정과 함께 봐야 희석 여부를 알 수 있습니다.",
        "https://docs.coinmetrics.io/",
    ),
    IndicatorSpec(
        "xrp_dominance",
        "리플 도미넌스",
        "XRP Dominance",
        "crypto",
        "positioning",
        "coingecko",
        "market_cap_percentage.xrp",
        "%",
        "daily",
        "context",
        1,
        "전체 코인 시가총액 중 리플 비중",
        "특정 알트코인으로의 자금 쏠림을 보는 보조지표입니다.",
        "https://www.coingecko.com/",
    ),
    IndicatorSpec(
        "xrp_funding_rate",
        "리플 무기한선물 펀딩비",
        "XRP Perpetual Funding Rate",
        "crypto",
        "positioning",
        "binance",
        "XRPUSDT:fundingRate",
        "%",
        "daily",
        "context",
        1,
        "Binance XRPUSDT 무기한선물의 일평균 펀딩비",
        "레버리지 쏠림을 확인하는 포지셔닝 지표입니다.",
        "https://www.binance.com/en/futures/XRPUSDT",
    ),
    IndicatorSpec(
        "xrp_open_interest",
        "리플 선물 미결제약정",
        "XRP Futures Open Interest",
        "crypto",
        "positioning",
        "binance",
        "XRPUSDT:openInterest",
        "USD",
        "daily",
        "risk_off",
        1,
        "Binance XRPUSDT 선물 미결제약정 달러 가치",
        "가격과 함께 급증하면 청산 위험이 커질 수 있습니다.",
        "https://www.binance.com/en/futures/XRPUSDT",
    ),
    IndicatorSpec(
        "equity_forward_pe",
        "주식시장 선행 PER",
        "Equity Forward P/E",
        "equity",
        "valuation",
        "licensed",
        "FORWARD_PE",
        "ratio",
        "daily",
        "context",
        3,
        "시장 또는 유니버스의 12개월 선행 PER",
        "실질금리와 이익 추정치 방향을 함께 비교합니다.",
        "",
        "licensed",
    ),
    IndicatorSpec(
        "equity_free_cash_flow_yield",
        "주식시장 FCF 수익률",
        "Equity Free Cash Flow Yield",
        "equity",
        "valuation",
        "licensed",
        "FCF_YIELD",
        "%",
        "quarterly",
        "risk_on",
        2,
        "유니버스의 잉여현금흐름 수익률",
        "회계이익보다 실제 현금 창출력에 가까운 밸류에이션입니다.",
        "",
        "licensed",
    ),
    IndicatorSpec(
        "equity_earnings_yield_gap",
        "주식 이익수익률 갭",
        "Equity Earnings Yield Gap",
        "equity",
        "valuation",
        "licensed",
        "EARNINGS_YIELD_GAP",
        "%p",
        "daily",
        "risk_on",
        3,
        "선행 이익수익률에서 국채 실질금리를 차감한 값",
        "주식이 채권 대비 제공하는 보상을 추정합니다.",
        "",
        "licensed",
    ),
)


class IndicatorProvider(Protocol):
    provider_name: str

    def collect(
        self, specs: tuple[IndicatorSpec, ...], start_date: date, end_date: date, run_id: UUID, collected_at: datetime
    ) -> list[IndicatorObservation]: ...


class FredCsvProvider:
    provider_name = "fred"

    def __init__(self, session: Any | None = None, timeout: float = 20.0, workers: int = 6) -> None:
        self.session = session
        self.timeout = timeout
        self.workers = workers
        self.errors: list[str] = []

    def collect(self, specs, start_date, end_date, run_id, collected_at) -> list[IndicatorObservation]:
        rows: list[IndicatorObservation] = []
        self.errors = []
        spec_batches = [specs[index : index + 8] for index in range(0, len(specs), 8)]
        with ThreadPoolExecutor(max_workers=min(self.workers, max(len(spec_batches), 1))) as executor:
            batches = executor.map(
                lambda batch: self._collect_batch(batch, start_date, end_date, run_id, collected_at), spec_batches
            )
            for batch in batches:
                rows.extend(batch)
        return rows

    def _collect_batch(self, specs, start_date, end_date, run_id, collected_at) -> list[IndicatorObservation]:
        rows: list[IndicatorObservation] = []
        response = self._get_batch(specs, start_date, end_date)
        if response is None:
            return rows
        texts = response.texts if hasattr(response, "texts") else (response.text,)
        for text in texts:
            for item in csv.DictReader(io.StringIO(text)):
                period = date.fromisoformat(item["observation_date"])
                if not (start_date <= period <= end_date):
                    continue
                for spec in specs:
                    raw_value = item.get(spec.source_series)
                    if not raw_value or raw_value == ".":
                        continue
                    rows.append(
                        _observation(
                            spec,
                            period,
                            float(raw_value) * spec.scale,
                            run_id,
                            collected_at,
                        )
                    )
        return rows

    def _get_batch(self, specs: tuple[IndicatorSpec, ...], start_date: date, end_date: date):
        last_error: Exception | None = None
        params = {
            "id": ",".join(spec.source_series for spec in specs),
            "cosd": start_date.isoformat(),
            "coed": end_date.isoformat(),
        }
        for _attempt in range(3):
            try:
                if self.session is not None:
                    response = self.session.get(
                        FRED_GRAPH_URL,
                        params=params,
                        timeout=self.timeout,
                        headers={"User-Agent": "Cluefin/0.1 market-indicator-collector"},
                    )
                    response.raise_for_status()
                    return response
                request = Request(
                    f"{FRED_GRAPH_URL}?{urlencode(params)}",
                    headers={"User-Agent": "Mozilla/5.0 Cluefin/0.1"},
                )
                with urlopen(request, timeout=self.timeout) as response:
                    body = response.read()
                    if response.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
                        body = gzip.decompress(body)
                if body[:4] == b"PK\x03\x04":
                    with ZipFile(io.BytesIO(body)) as archive:
                        texts = tuple(
                            archive.read(name).decode("utf-8-sig")
                            for name in archive.namelist()
                            if name.lower().endswith(".csv")
                        )
                else:
                    texts = (body.decode("utf-8-sig"),)
                return _CsvPayload(texts)
            except (requests.RequestException, OSError, TimeoutError) as exc:
                last_error = exc
        self.errors.append(f"{','.join(spec.source_series for spec in specs)}: {last_error}")
        return None


@dataclass(frozen=True, slots=True)
class _CsvPayload:
    texts: tuple[str, ...]


class DefiLlamaProvider:
    provider_name = "defillama"

    def __init__(self, session: Any | None = None, timeout: float = 45.0) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.errors: list[str] = []

    def collect(self, specs, start_date, end_date, run_id, collected_at) -> list[IndicatorObservation]:
        by_id = {spec.indicator_id: spec for spec in specs}
        rows: list[IndicatorObservation] = []
        self.errors = []
        if "stablecoin_supply" in by_id:
            payload = self._safe_json(DEFILLAMA_STABLECOIN_URL, "stablecoin_supply")
            if payload is not None:
                for item in payload:
                    period = datetime.fromtimestamp(int(item["date"]), tz=timezone.utc).date()
                    value = item.get("totalCirculatingUSD", {}).get("peggedUSD")
                    if value is not None and start_date <= period <= end_date:
                        rows.append(
                            _observation(by_id["stablecoin_supply"], period, float(value), run_id, collected_at)
                        )
        if "defi_tvl" in by_id:
            payload = self._safe_json(DEFILLAMA_TVL_URL, "defi_tvl")
            if payload is not None:
                for item in payload:
                    period = datetime.fromtimestamp(int(item["date"]), tz=timezone.utc).date()
                    if start_date <= period <= end_date:
                        rows.append(_observation(by_id["defi_tvl"], period, float(item["tvl"]), run_id, collected_at))
        for indicator_id, data_type in (("defi_fees", "dailyFees"), ("defi_revenue", "dailyRevenue")):
            if indicator_id not in by_id:
                continue
            payload = self._safe_json(
                DEFILLAMA_FEES_URL,
                indicator_id,
                params={
                    "excludeTotalDataChart": "false",
                    "excludeTotalDataChartBreakdown": "true",
                    "dataType": data_type,
                },
            )
            if payload is not None:
                for timestamp, value in payload.get("totalDataChart", []):
                    period = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date()
                    if value is not None and start_date <= period <= end_date:
                        rows.append(_observation(by_id[indicator_id], period, float(value), run_id, collected_at))
        return rows

    def _json(self, url: str, params: dict[str, str] | None = None) -> Any:
        response = self.session.get(url, params=params, timeout=self.timeout, headers={"User-Agent": "Cluefin/0.1"})
        response.raise_for_status()
        return response.json()

    def _safe_json(self, url: str, label: str, params: dict[str, str] | None = None) -> Any | None:
        try:
            return self._json(url, params=params)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            self.errors.append(f"{label}: {type(exc).__name__}")
            return None


class CoinMetricsProvider:
    provider_name = "coinmetrics"

    def __init__(self, session: Any | None = None, timeout: float = 30.0) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.errors: list[str] = []

    def collect(self, specs, start_date, end_date, run_id, collected_at) -> list[IndicatorObservation]:
        grouped: dict[str, list[IndicatorSpec]] = defaultdict(list)
        self.errors = []
        for spec in specs:
            asset, _metric = spec.source_series.split(":", 1)
            grouped[asset].append(spec)
        rows: list[IndicatorObservation] = []
        for asset, asset_specs in grouped.items():
            metrics = [spec.source_series.split(":", 1)[1] for spec in asset_specs]
            try:
                response = self.session.get(
                    COIN_METRICS_URL,
                    params={
                        "assets": asset,
                        "metrics": ",".join(metrics),
                        "frequency": "1d",
                        "start_time": start_date.isoformat(),
                        "end_time": end_date.isoformat(),
                        "page_size": 10000,
                    },
                    timeout=self.timeout,
                    headers={"User-Agent": "Cluefin/0.1"},
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"].get("message") or payload["error"]))
            except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as exc:
                self.errors.append(f"{asset}: {type(exc).__name__}")
                continue
            for item in payload.get("data", []):
                period = date.fromisoformat(str(item["time"])[:10])
                for spec in asset_specs:
                    metric = spec.source_series.split(":", 1)[1]
                    if item.get(metric) is not None:
                        rows.append(
                            _observation(spec, period, float(item[metric]), run_id, collected_at, {"asset": asset})
                        )
        return rows


class CoinGeckoProvider:
    provider_name = "coingecko"

    def __init__(self, session: Any | None = None, timeout: float = 30.0) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.errors: list[str] = []

    def collect(self, specs, start_date, end_date, run_id, collected_at) -> list[IndicatorObservation]:
        self.errors = []
        rows: list[IndicatorObservation] = []
        global_specs = [spec for spec in specs if not spec.source_series.startswith("coin.")]
        if global_specs:
            try:
                response = self.session.get(
                    COINGECKO_GLOBAL_URL, timeout=self.timeout, headers={"User-Agent": "Cluefin/0.1"}
                )
                response.raise_for_status()
                data = response.json()["data"]
                values = {
                    "total_market_cap.usd": data["total_market_cap"]["usd"],
                    "total_volume.usd": data["total_volume"]["usd"],
                    "market_cap_change_percentage_24h_usd": data["market_cap_change_percentage_24h_usd"],
                }
                # Dominance is exposed per coin, so any market_cap_percentage.<coin> spec resolves here.
                for coin, share in (data.get("market_cap_percentage") or {}).items():
                    values[f"market_cap_percentage.{coin}"] = share
                period = collected_at.date()
                if start_date <= period <= end_date:
                    for spec in global_specs:
                        if spec.source_series not in values:
                            self.errors.append(f"{spec.indicator_id}: series not in global response")
                            continue
                        rows.append(_observation(spec, period, float(values[spec.source_series]), run_id, collected_at))
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                self.errors.append(f"global: {type(exc).__name__}")

        gold_spec = next((spec for spec in specs if spec.indicator_id == "gold"), None)
        if gold_spec is not None:
            try:
                days = max(1, min(365, (end_date - start_date).days + 2))
                response = self.session.get(
                    COINGECKO_PAXG_URL,
                    params={"vs_currency": "usd", "days": days, "interval": "daily"},
                    timeout=self.timeout,
                    headers={"User-Agent": "Cluefin/0.1"},
                )
                response.raise_for_status()
                for timestamp, value in response.json().get("prices", []):
                    period = datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc).date()
                    if start_date <= period <= end_date:
                        rows.append(
                            _observation(
                                gold_spec,
                                period,
                                float(value),
                                run_id,
                                collected_at,
                                {"proxy_asset": "PAXG"},
                            )
                        )
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                self.errors.append(f"gold: {type(exc).__name__}")
        return rows


class BinanceFuturesProvider:
    provider_name = "binance"

    def __init__(self, session: Any | None = None, timeout: float = 30.0) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.errors: list[str] = []

    def collect(self, specs, start_date, end_date, run_id, collected_at) -> list[IndicatorObservation]:
        self.errors = []
        rows: list[IndicatorObservation] = []
        for spec in specs:
            symbol, _, field = spec.source_series.partition(":")
            try:
                if field == "fundingRate":
                    rows.extend(self._funding(spec, symbol, start_date, end_date, run_id, collected_at))
                elif field == "openInterest":
                    rows.extend(self._open_interest(spec, symbol, start_date, end_date, run_id, collected_at))
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                self.errors.append(f"{spec.indicator_id}: {type(exc).__name__}")
        return rows

    def _funding(self, spec, symbol, start_date, end_date, run_id, collected_at) -> list[IndicatorObservation]:
        start_ms = int(datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc).timestamp() * 1000)
        funding_by_date: dict[date, list[float]] = defaultdict(list)
        while start_ms <= end_ms:
            response = self.session.get(
                BINANCE_FUNDING_URL,
                params={"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not payload:
                break
            for item in payload:
                period = datetime.fromtimestamp(int(item["fundingTime"]) / 1000, tz=timezone.utc).date()
                funding_by_date[period].append(float(item["fundingRate"]) * 100)
            last_time = int(payload[-1]["fundingTime"])
            if len(payload) < 1000 or last_time >= end_ms:
                break
            start_ms = last_time + 1
        return [
            _observation(
                spec,
                period,
                sum(values) / len(values),
                run_id,
                collected_at,
                {"symbol": symbol, "samples": len(values)},
            )
            for period, values in sorted(funding_by_date.items())
        ]

    def _open_interest(self, spec, symbol, start_date, end_date, run_id, collected_at) -> list[IndicatorObservation]:
        response = self.session.get(
            BINANCE_OPEN_INTEREST_URL,
            params={"symbol": symbol, "period": "1d", "limit": 30},
            timeout=self.timeout,
        )
        response.raise_for_status()
        rows: list[IndicatorObservation] = []
        for item in response.json():
            period = datetime.fromtimestamp(int(item["timestamp"]) / 1000, tz=timezone.utc).date()
            if start_date <= period <= end_date:
                rows.append(
                    _observation(
                        spec,
                        period,
                        float(item["sumOpenInterestValue"]),
                        run_id,
                        collected_at,
                        {"symbol": symbol},
                    )
                )
        return rows


class EcosProvider:
    provider_name = "ecos"

    def __init__(self, api_key: str | None = None, session: Any | None = None, timeout: float = 30.0) -> None:
        self.api_key = api_key or os.getenv("BOK_ECOS_API_KEY") or "sample"
        self.session = session or requests.Session()
        self.timeout = timeout

    def collect(self, specs, start_date, end_date, run_id, collected_at) -> list[IndicatorObservation]:
        rows: list[IndicatorObservation] = []
        for spec in specs:
            parts = spec.source_series.split(":")
            stat_code, cycle, item_code = parts[:3]
            transform = parts[3] if len(parts) > 3 else "level"
            # A monthly period almost never falls inside a daily collection window, and ECOS
            # publishes with a lag, so monthly series look back a few months instead.
            window_start = _shift_months(start_date, -MONTHLY_LOOKBACK_MONTHS) if cycle == "M" else start_date
            request_start = (
                date(window_start.year - 1, window_start.month, 1)
                if transform == "yoy" and cycle == "M"
                else window_start
            )
            source_rows = self._search(
                stat_code=stat_code,
                cycle=cycle,
                start=_ecos_period(request_start, cycle),
                end=_ecos_period(end_date, cycle),
                item_code=item_code,
            )
            raw_values = {
                _parse_ecos_period(str(item["TIME"]), cycle): float(str(item["DATA_VALUE"]).replace(",", ""))
                for item in source_rows
            }
            for item in source_rows:
                period = _parse_ecos_period(str(item["TIME"]), cycle)
                value = raw_values[period]
                if transform == "yoy":
                    previous = raw_values.get(date(period.year - 1, period.month, 1))
                    if previous in {None, 0}:
                        continue
                    value = (value - previous) / abs(previous) * 100
                if window_start <= period <= end_date:
                    rows.append(
                        _observation(
                            spec,
                            period,
                            value,
                            run_id,
                            collected_at,
                            {
                                "unit_name": item.get("UNIT_NAME"),
                                "item_name": item.get("ITEM_NAME1"),
                                "transform": transform,
                            },
                        )
                    )
        return rows

    def _search(self, *, stat_code: str, cycle: str, start: str, end: str, item_code: str) -> list[dict]:
        page_size = 10 if self.api_key == "sample" else 1000
        start_row = 1
        rows: list[dict] = []
        total = 1
        while start_row <= total:
            end_row = start_row + page_size - 1
            url = (
                f"https://ecos.bok.or.kr/api/StatisticSearch/{self.api_key}/json/kr/{start_row}/{end_row}/"
                f"{stat_code}/{cycle}/{start}/{end}/{item_code}"
            )
            try:
                response = self.session.get(url, timeout=self.timeout, headers={"User-Agent": "Cluefin/0.1"})
            except requests.RequestException as exc:
                raise RuntimeError(f"ECOS request failed: {type(exc).__name__}") from None
            if getattr(response, "status_code", 200) != 200:
                raise RuntimeError(f"ECOS HTTP {response.status_code}")
            payload = response.json()
            if payload.get("RESULT"):
                result_code = str(payload["RESULT"].get("CODE") or "")
                message = str(payload["RESULT"].get("MESSAGE") or payload["RESULT"])
                # INFO-200 means the window simply holds no rows; that is an empty result, not a failure.
                if result_code == "INFO-200" or "해당하는 데이터가 없습니다" in message:
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


class KoreaCustomsProvider:
    provider_name = "customs"
    list_url = "https://www.customs.go.kr/kcs/na/ntt/selectNttList.do"
    detail_url = "https://www.customs.go.kr/kcs/na/ntt/selectNttInfo.do"

    def __init__(self, session: Any | None = None, timeout: float = 30.0, workers: int = 5) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.workers = workers
        self.errors: list[str] = []

    def collect(self, specs, start_date, end_date, run_id, collected_at) -> list[IndicatorObservation]:
        self.errors = []
        spec = next((item for item in specs if item.indicator_id == "kr_exports_20d"), None)
        if spec is None:
            return []
        headers = {"User-Agent": "Mozilla/5.0 Cluefin/0.1", "Referer": "https://www.customs.go.kr/kcs/main.do"}
        try:
            response = self.session.get(
                self.list_url,
                params={
                    "mi": "2891",
                    "bbsId": "1362",
                    "searchType": "sj",
                    "searchValue": "수출입 현황",
                    "listCo": "50",
                },
                timeout=self.timeout,
                headers=headers,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Korea Customs list request failed: {type(exc).__name__}") from None
        parser = _CustomsReleaseParser()
        parser.feed(response.text)
        releases = [item for item in parser.releases if start_date <= item["period"] <= end_date]
        with ThreadPoolExecutor(max_workers=min(self.workers, max(len(releases), 1))) as executor:
            observations = list(
                executor.map(
                    lambda release: self._fetch_release(release, spec, run_id, collected_at, headers), releases
                )
            )
        return [row for row in observations if row is not None]

    def _fetch_release(self, release, spec, run_id, collected_at, headers) -> IndicatorObservation | None:
        try:
            response = self.session.get(
                self.detail_url,
                params={
                    "mi": "2891",
                    "bbsId": "1362",
                    "nttSn": release["ntt_sn"],
                    "nttSnUrl": release["ntt_url"],
                },
                timeout=self.timeout,
                headers=headers,
            )
            response.raise_for_status()
            value = _parse_customs_export_growth(response.text)
        except (requests.RequestException, ValueError) as exc:
            self.errors.append(f"{release['period'].isoformat()}: {type(exc).__name__}")
            return None
        return _observation(
            spec,
            release["period"],
            value,
            run_id,
            collected_at,
            {
                "title": release["title"],
                "ntt_sn": release["ntt_sn"],
                "source_url": response.url,
            },
        )


class DartAggregateProvider:
    provider_name = "dart"

    def __init__(
        self,
        store: ClickHouseStore | None = None,
        api_key: str | None = None,
        session: Any | None = None,
        timeout: float = 30.0,
        workers: int = 5,
    ) -> None:
        self.store = store
        self.api_key = api_key or os.getenv("DART_AUTH_KEY")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.workers = workers
        self.errors: list[str] = []

    def collect(self, specs, start_date, end_date, run_id, collected_at) -> list[IndicatorObservation]:
        if not self.api_key:
            raise RuntimeError("DART_AUTH_KEY is not configured")
        if self.store is None:
            raise RuntimeError("DART aggregate provider requires a ClickHouse store")
        symbols = self._latest_universe_symbols()
        if not symbols:
            raise RuntimeError("No latest universe symbols are available for DART aggregation")
        corp_by_symbol = self._corp_codes(symbols)
        business_year = str(end_date.year - 1)
        with ThreadPoolExecutor(max_workers=min(self.workers, max(len(corp_by_symbol), 1))) as executor:
            snapshots = list(
                executor.map(
                    lambda item: self._company_snapshot(item[0], item[1], business_year),
                    corp_by_symbol.items(),
                )
            )
        valid = [item for item in snapshots if item]
        if not valid:
            raise RuntimeError("DART returned no usable financial snapshots")

        revenue_growth = [item["revenue_growth"] for item in valid if item.get("revenue_growth") is not None]
        operating_growth = [
            item["operating_profit_growth"] for item in valid if item.get("operating_profit_growth") is not None
        ]
        roe_values = [item["roe"] for item in valid if item.get("roe") is not None]
        debt_values = [item["debt_ratio"] for item in valid if item.get("debt_ratio") is not None]
        aggregates = {
            "kr_revenue_growth_breadth": _positive_breadth(revenue_growth),
            "kr_operating_profit_growth_breadth": _positive_breadth(operating_growth),
            "kr_roe_median": median(roe_values) if roe_values else None,
            "kr_debt_ratio_median": median(debt_values) if debt_values else None,
        }
        spec_by_id = {spec.indicator_id: spec for spec in specs}
        period = date(int(business_year), 12, 31)
        rows: list[IndicatorObservation] = []
        for indicator_id, value in aggregates.items():
            if value is None or indicator_id not in spec_by_id:
                continue
            sample = {
                "kr_revenue_growth_breadth": len(revenue_growth),
                "kr_operating_profit_growth_breadth": len(operating_growth),
                "kr_roe_median": len(roe_values),
                "kr_debt_ratio_median": len(debt_values),
            }[indicator_id]
            rows.append(
                _observation(
                    spec_by_id[indicator_id],
                    period,
                    float(value),
                    run_id,
                    collected_at,
                    {"business_year": business_year, "sample_count": sample, "universe_size": len(symbols)},
                )
            )
        return rows

    def _latest_universe_symbols(self) -> tuple[str, ...]:
        result = self.store.client().query(
            """
            SELECT DISTINCT symbol
            FROM market.daily_universe_members FINAL
            WHERE trade_date = (SELECT max(trade_date) FROM market.daily_universe_members)
                AND match(symbol, '^[0-9]{6}$')
            ORDER BY symbol
            """
        )
        return tuple(str(row[0]) for row in result.result_rows)

    def _corp_codes(self, symbols: tuple[str, ...]) -> dict[str, str]:
        try:
            response = self.session.get(
                "https://opendart.fss.or.kr/api/corpCode.xml",
                params={"crtfc_key": self.api_key},
                timeout=self.timeout,
                headers={"User-Agent": "Cluefin/0.1"},
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"DART corp-code request failed: {type(exc).__name__}") from None
        if getattr(response, "status_code", 200) != 200:
            raise RuntimeError(f"DART corp-code HTTP {response.status_code}")
        with ZipFile(io.BytesIO(response.content)) as archive:
            xml_name = next(name for name in archive.namelist() if name.lower().endswith(".xml"))
            root = ElementTree.fromstring(archive.read(xml_name))
        wanted = set(symbols)
        mapping: dict[str, str] = {}
        for item in root.findall("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_code = (item.findtext("corp_code") or "").strip()
            if stock_code in wanted and corp_code:
                mapping[stock_code] = corp_code
        return mapping

    def _company_snapshot(self, symbol: str, corp_code: str, business_year: str) -> dict[str, float] | None:
        try:
            response = self.session.get(
                "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json",
                params={
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bsns_year": business_year,
                    "reprt_code": "11011",
                },
                timeout=self.timeout,
                headers={"User-Agent": "Cluefin/0.1"},
            )
            if getattr(response, "status_code", 200) != 200:
                self.errors.append(f"{symbol}: HTTP {response.status_code}")
                return None
            payload = response.json()
            if payload.get("status") != "000":
                self.errors.append(f"{symbol}: DART status {payload.get('status', 'unknown')}")
                return None
            records = payload.get("list") or []
            consolidated = [item for item in records if item.get("fs_div") == "CFS"]
            return _dart_snapshot(consolidated or records)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            self.errors.append(f"{symbol}: {type(exc).__name__}")
            return None


class LicensedMetricsProvider:
    """PAT-authenticated adapter for contracted metrics using a normalized JSON contract."""

    provider_name = "licensed"

    def __init__(
        self,
        *,
        url: str | None = None,
        pat: str | None = None,
        session: Any | None = None,
        timeout: float = 45.0,
    ) -> None:
        self.url = url or os.getenv("CLUEFIN_VENDOR_METRICS_URL", "")
        self.pat = pat or os.getenv("CLUEFIN_VENDOR_PAT", "")
        self.session = session or requests.Session()
        self.timeout = timeout

    def collect(self, specs, start_date, end_date, run_id, collected_at) -> list[IndicatorObservation]:
        missing = [
            name
            for name, value in (("CLUEFIN_VENDOR_METRICS_URL", self.url), ("CLUEFIN_VENDOR_PAT", self.pat))
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing licensed feed settings: {', '.join(missing)}")
        try:
            response = self.session.get(
                self.url,
                params={
                    "indicator_ids": ",".join(spec.indicator_id for spec in specs),
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
                headers={"Authorization": f"Bearer {self.pat}", "Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Licensed metrics request failed: {type(exc).__name__}") from None
        if getattr(response, "status_code", 200) != 200:
            raise RuntimeError(f"Licensed metrics HTTP {response.status_code}")
        payload = response.json()
        data = payload.get("data", payload if isinstance(payload, list) else [])
        specs_by_id = {spec.indicator_id: spec for spec in specs}
        rows: list[IndicatorObservation] = []
        for index, item in enumerate(data):
            indicator_id = str(item.get("indicator_id") or "")
            spec = specs_by_id.get(indicator_id)
            if spec is None:
                continue
            try:
                period = date.fromisoformat(str(item["period"])[:10])
                value = float(item["value"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"Invalid licensed metric row at index {index}") from exc
            if start_date <= period <= end_date:
                rows.append(
                    _observation(
                        spec,
                        period,
                        value,
                        run_id,
                        collected_at,
                        {"vendor": item.get("vendor"), **(item.get("metadata") or {})},
                    )
                )
        return rows


def collect_market_indicators(
    *,
    store: ClickHouseStore,
    providers: tuple[IndicatorProvider, ...],
    start_date: date,
    end_date: date,
    run_id: UUID,
    collected_at: datetime,
    catalog: tuple[IndicatorSpec, ...] = INDICATOR_CATALOG,
    strict: bool = False,
) -> dict[str, Any]:
    definitions = [spec.definition(collected_at) for spec in catalog]
    definition_count = store.insert_records("market.indicator_definitions", definitions)
    observations: list[IndicatorObservation] = []
    provider_counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    for provider in providers:
        specs = tuple(
            spec
            for spec in catalog
            if spec.provider == provider.provider_name
            and (
                spec.availability in {"public", "api_key"}
                or (provider.provider_name == "licensed" and spec.availability == "licensed")
            )
        )
        try:
            provider_rows = provider.collect(specs, start_date, end_date, run_id, collected_at)
        except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as exc:
            if strict:
                raise
            provider_rows = []
            errors[provider.provider_name] = str(exc)
        provider_issues = getattr(provider, "errors", None)
        if provider_issues:
            message = "; ".join(provider_issues)
            if strict:
                raise RuntimeError(message)
            errors[provider.provider_name] = message
        observations.extend(provider_rows)
        provider_counts[provider.provider_name] = len(provider_rows)

    derived = _derive_observations(observations, catalog, run_id, collected_at)
    observations.extend(derived)
    provider_counts["derived"] = len(derived)
    inserted = store.insert_records("market.indicator_observations", observations)
    return {
        "run_id": str(run_id),
        "definitions": definition_count,
        "observations": inserted,
        "providers": provider_counts,
        "errors": errors,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }


def default_indicator_providers(store: ClickHouseStore | None = None) -> tuple[IndicatorProvider, ...]:
    return (
        FredCsvProvider(),
        DefiLlamaProvider(),
        CoinMetricsProvider(),
        CoinGeckoProvider(),
        BinanceFuturesProvider(),
        KoreaCustomsProvider(),
        EcosProvider(),
        DartAggregateProvider(store=store),
        LicensedMetricsProvider(),
    )


def catalog_summary(catalog: tuple[IndicatorSpec, ...] = INDICATOR_CATALOG) -> dict[str, Any]:
    providers: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for spec in catalog:
        providers[spec.provider][spec.availability] += 1
    return {
        "total": len(catalog),
        "providers": {provider: dict(statuses) for provider, statuses in sorted(providers.items())},
        "domains": sorted({spec.domain for spec in catalog}),
        "categories": sorted({spec.category for spec in catalog}),
    }


def import_indicator_csv(
    *,
    store: ClickHouseStore,
    csv_text: str,
    run_id: UUID,
    collected_at: datetime,
    catalog: tuple[IndicatorSpec, ...] = INDICATOR_CATALOG,
) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(csv_text))
    required = {"indicator_id", "period", "value"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("Indicator CSV requires indicator_id, period, and value columns")
    specs = {spec.indicator_id: spec for spec in catalog}
    observations: list[IndicatorObservation] = []
    for line_number, item in enumerate(reader, start=2):
        indicator_id = str(item.get("indicator_id") or "").strip()
        spec = specs.get(indicator_id)
        if spec is None:
            raise ValueError(f"Unknown indicator_id on CSV line {line_number}: {indicator_id}")
        try:
            period = date.fromisoformat(str(item.get("period") or ""))
            value = float(str(item.get("value") or ""))
        except ValueError as exc:
            raise ValueError(f"Invalid period or value on CSV line {line_number}") from exc
        metadata: dict[str, Any] = {"imported": True}
        raw_metadata = str(item.get("metadata_json") or "").strip()
        if raw_metadata:
            try:
                parsed = json.loads(raw_metadata)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid metadata_json on CSV line {line_number}") from exc
            if not isinstance(parsed, dict):
                raise ValueError(f"metadata_json must be an object on CSV line {line_number}")
            metadata.update(parsed)
        observations.append(
            IndicatorObservation(
                period=period,
                indicator_id=indicator_id,
                provider=str(item.get("provider") or spec.provider).strip(),
                value=value,
                metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                run_id=run_id,
                collected_at=collected_at,
            )
        )
    definitions = [spec.definition(collected_at) for spec in catalog]
    return {
        "run_id": str(run_id),
        "definitions": store.insert_records("market.indicator_definitions", definitions),
        "observations": store.insert_records("market.indicator_observations", observations),
        "indicator_ids": sorted({row.indicator_id for row in observations}),
    }


def _observation(
    spec: IndicatorSpec,
    period: date,
    value: float,
    run_id: UUID,
    collected_at: datetime,
    metadata: dict[str, Any] | None = None,
) -> IndicatorObservation:
    return IndicatorObservation(
        period=period,
        indicator_id=spec.indicator_id,
        provider=spec.provider,
        value=value,
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
        run_id=run_id,
        collected_at=collected_at,
    )


def _derive_observations(
    observations: list[IndicatorObservation],
    catalog: tuple[IndicatorSpec, ...],
    run_id: UUID,
    collected_at: datetime,
) -> list[IndicatorObservation]:
    values: dict[str, dict[date, float]] = defaultdict(dict)
    for row in observations:
        values[row.indicator_id][row.period] = row.value
    derived: list[IndicatorObservation] = []
    catalog_by_id = {item.indicator_id: item for item in catalog}
    # 카탈로그의 일부만 넘어올 수 있다(예: 환율만 도는 작업). 없는 파생 지표는 조용히 건너뛴다.
    liquidity_spec = catalog_by_id.get("fed_net_liquidity")
    if liquidity_spec is not None:
        common_periods = (
            set(values["fed_total_assets"]) & set(values["us_treasury_tga"]) & set(values["fed_overnight_rrp"])
        )
        derived.extend(
            _observation(
                liquidity_spec,
                period,
                values["fed_total_assets"][period]
                - values["us_treasury_tga"][period]
                - values["fed_overnight_rrp"][period],
                run_id,
                collected_at,
                {"formula": "WALCL/1000 - WTREGEN/1000 - RRPONTSYD"},
            )
            for period in sorted(common_periods)
        )
    for asset in ("btc", "eth", "xrp"):
        price_id, mvrv_id = f"{asset}_price_usd", f"{asset}_mvrv"
        realized_spec = catalog_by_id.get(f"{asset}_realized_price")
        if realized_spec is not None:
            derived.extend(
                _observation(
                    realized_spec,
                    period,
                    values[price_id][period] / values[mvrv_id][period],
                    run_id,
                    collected_at,
                    {"formula": "PriceUSD / CapMVRVCur", "asset": asset},
                )
                for period in sorted(set(values[price_id]) & set(values[mvrv_id]))
                if values[mvrv_id][period]
            )
        nupl_spec = catalog_by_id.get(f"{asset}_nupl")
        if nupl_spec is not None:
            derived.extend(
                _observation(
                    nupl_spec,
                    period,
                    1 - (1 / values[mvrv_id][period]),
                    run_id,
                    collected_at,
                    {"formula": "1 - 1 / CapMVRVCur", "asset": asset},
                )
                for period in sorted(values[mvrv_id])
                if values[mvrv_id][period]
            )
    density_spec = catalog_by_id.get("eth_active_addresses_ratio")
    if density_spec is not None:
        derived.extend(
            _observation(
                density_spec,
                period,
                values["eth_active_addresses"][period] / values["eth_transactions"][period],
                run_id,
                collected_at,
                {"formula": "AdrActCnt / TxCnt", "asset": "eth"},
            )
            for period in sorted(set(values["eth_active_addresses"]) & set(values["eth_transactions"]))
            if values["eth_transactions"][period]
        )
    eth_supply_spec = catalog_by_id.get("protocol_token_inflation")
    eth_periods = sorted(values["eth_supply"]) if eth_supply_spec is not None else []
    for index in range(30, len(eth_periods)):
        period = eth_periods[index]
        previous_period = eth_periods[index - 30]
        previous_supply = values["eth_supply"][previous_period]
        if not previous_supply:
            continue
        derived.append(
            _observation(
                eth_supply_spec,
                period,
                (values["eth_supply"][period] / previous_supply - 1) * 100,
                run_id,
                collected_at,
                {"formula": "SplyCur / SplyCur[-30d] - 1", "previous_period": previous_period.isoformat()},
            )
        )
    return derived


def _shift_months(value: date, months: int) -> date:
    total = value.year * 12 + (value.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def _ecos_period(period: date, cycle: str) -> str:
    if cycle == "D":
        return period.strftime("%Y%m%d")
    if cycle == "M":
        return period.strftime("%Y%m")
    if cycle == "A":
        return period.strftime("%Y")
    raise ValueError(f"Unsupported ECOS cycle: {cycle}")


def _parse_ecos_period(value: str, cycle: str) -> date:
    if cycle == "D":
        return datetime.strptime(value, "%Y%m%d").date()
    if cycle == "M":
        return datetime.strptime(value, "%Y%m").date()
    if cycle == "A":
        return date(int(value), 1, 1)
    raise ValueError(f"Unsupported ECOS cycle: {cycle}")


def _dart_snapshot(records: list[dict[str, Any]]) -> dict[str, float]:
    current: dict[str, float] = {}
    previous: dict[str, float] = {}
    for item in records:
        name = str(item.get("account_nm") or "").replace(" ", "")
        metric = _dart_metric_id(name)
        if metric is None or metric in current:
            continue
        current_value = _dart_amount(item.get("thstrm_amount"))
        previous_value = _dart_amount(item.get("frmtrm_amount"))
        if current_value is not None:
            current[metric] = current_value
        if previous_value is not None:
            previous[metric] = previous_value

    equity = current.get("equity")
    return {
        "revenue_growth": _growth_rate(current.get("revenue"), previous.get("revenue")),
        "operating_profit_growth": _growth_rate(current.get("operating_profit"), previous.get("operating_profit")),
        "roe": current["net_income"] / equity * 100 if equity and current.get("net_income") is not None else None,
        "debt_ratio": current["liabilities"] / equity * 100
        if equity and current.get("liabilities") is not None
        else None,
    }


def _dart_metric_id(name: str) -> str | None:
    if name in {"매출액", "수익(매출액)", "영업수익", "매출"}:
        return "revenue"
    if "영업이익" in name and "손실" not in name:
        return "operating_profit"
    if name in {"당기순이익", "당기순이익(손실)", "연결당기순이익"}:
        return "net_income"
    if name == "부채총계":
        return "liabilities"
    if name == "자본총계":
        return "equity"
    return None


def _dart_amount(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _growth_rate(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in {None, 0}:
        return None
    return (current - previous) / abs(previous) * 100


def _positive_breadth(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(value > 0 for value in values) / len(values) * 100


class _CustomsReleaseParser(HTMLParser):
    title_pattern = re.compile(
        r"(?P<year>\d{4})년\s*(?P<month>\d{1,2})월\s*1일\s*[~∼\-]\s*(?:\d{1,2}월\s*)?20일\s*수출입\s*현황"
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.releases: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        values = dict(attrs)
        classes = str(values.get("class") or "").split()
        title = str(values.get("title") or "")
        match = self.title_pattern.search(title)
        if "nttInfoBtn" not in classes or match is None:
            return
        self.releases.append(
            {
                "period": date(int(match.group("year")), int(match.group("month")), 20),
                "title": title,
                "ntt_sn": str(values.get("data-id") or ""),
                "ntt_url": str(values.get("data-url") or ""),
            }
        )


class _PageTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)


def _parse_customs_export_growth(html: str) -> float:
    parser = _PageTextParser()
    parser.feed(html)
    text = re.sub(r"\s+", " ", " ".join(parser.parts))
    match = re.search(
        r"수출\s*(?:은|:)?.{0,100}?전년\s*동기\s*대비\s*([△▲+\-]?\s*\d+(?:\.\d+)?)\s*%\s*(증가|감소)",
        text,
    )
    if match is None:
        raise ValueError("Could not parse first-20-days export growth")
    token = match.group(1).replace(" ", "")
    value = float(token.replace("△", "").replace("▲", "").replace("+", ""))
    return -abs(value) if "△" in token or token.startswith("-") or match.group(2) == "감소" else abs(value)
