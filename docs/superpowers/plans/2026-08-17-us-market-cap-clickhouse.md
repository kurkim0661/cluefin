# US Market Cap Top 50 ClickHouse Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a KIS-backed US market-cap top 50 provider to `cluefin-store backfill-top` and feed it through the existing ClickHouse daily analytics pipeline.

**Architecture:** Keep `backfill_top_ranked()` as the ingestion orchestrator. Add a focused `cluefin_store.kis_us` adapter that translates KIS overseas ranking and period-quote responses into `RankedSymbol` and `DailyOhlcv`, then extend the CLI provider switch without changing existing ClickHouse table contracts.

**Tech Stack:** Python 3.10, uv workspace, Click, pytest, ClickHouse row dataclasses, existing `cluefin-openapi` KIS client.

---

## File Structure

- Create `packages/cluefin-store/src/cluefin_store/kis_us.py`
  - Owns KIS US ranking merge, KIS daily quote normalization, Decimal parsing, and exchange lookup.
  - Exposes `KisUsMarketDataProvider`.
- Modify `packages/cluefin-store/src/cluefin_store/ingestion.py`
  - Make default universe naming market-aware and metric-aware.
  - Preserve Toss/KR existing default name.
- Modify `packages/cluefin-store/src/cluefin_store/cli.py`
  - Add provider choice `kis`.
  - Add `--market-country`.
  - Build `KisUsMarketDataProvider.from_env()` only outside `--dry-run`.
- Modify `packages/cluefin-store/pyproject.toml`
  - Add workspace dependency `cluefin-openapi`.
- Create `packages/cluefin-store/tests/test_kis_us_provider.py`
  - Unit tests for ranking merge and OHLCV normalization, using fake KIS clients only.
- Modify `packages/cluefin-store/tests/test_ingestion.py`
  - Unit tests for US market-cap universe naming and selection metric.
- Modify `packages/cluefin-store/tests/test_cli.py`
  - Unit tests for KIS dry-run and Toss backward compatibility.

## Task 1: Market-Aware Universe Naming

**Files:**
- Modify: `packages/cluefin-store/src/cluefin_store/ingestion.py`
- Modify: `packages/cluefin-store/tests/test_ingestion.py`

- [ ] **Step 1: Write the failing naming tests**

Append these tests to `packages/cluefin-store/tests/test_ingestion.py`:

```python
def test_us_market_cap_config_uses_current_universe_name() -> None:
    config = TopBackfillConfig(
        end_date=date(2026, 8, 17),
        years=1,
        count=50,
        ranking_type="MARKET_CAP",
        ranking_duration="current",
        market_country="US",
    )

    assert config.resolved_universe_name == "us_market_cap_top50_current"


def test_universe_members_use_market_cap_selection_metric_for_us() -> None:
    provider = FakeProvider()
    store = FakeStore()
    config = TopBackfillConfig(
        end_date=date(2026, 8, 17),
        years=1,
        count=2,
        ranking_type="MARKET_CAP",
        ranking_duration="current",
        market_country="US",
        warmup_calendar_days=0,
        outcome_horizons=(5,),
    )

    backfill_top_ranked(
        provider=provider,
        store=store,
        config=config,
        run_id=RUN_ID,
        collected_at=COLLECTED_AT,
    )

    member_rows = store.records["market.daily_universe_members"]
    assert member_rows[0].market_country == "US"
    assert member_rows[0].universe_name == "us_market_cap_top2_current"
    assert member_rows[0].selection_metric_name == "market_cap"
```

Replace `FakeStore` in the same test file with this version so tests can inspect records:

```python
class FakeStore:
    def __init__(self) -> None:
        self.inserts: dict[str, int] = {}
        self.records: dict[str, list] = {}

    def insert_records(self, table: str, records: list) -> int:
        self.inserts[table] = len(records)
        self.records[table] = list(records)
        return len(records)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest packages/cluefin-store/tests/test_ingestion.py -v
```

Expected: FAIL because the current universe name is `us_market_cap_top50_current` only after implementation; before implementation it resolves through the existing generic `kr_...` style or lacks stored `records`.

- [ ] **Step 3: Implement market-aware naming and metric mapping**

In `packages/cluefin-store/src/cluefin_store/ingestion.py`, update `TopBackfillConfig.resolved_universe_name`:

```python
    @property
    def resolved_universe_name(self) -> str:
        if self.universe_name:
            return self.universe_name
        country = self.market_country.lower()
        metric = self.ranking_type.lower()
        return f"{country}_{metric}_top{self.count}_{self.ranking_duration}"
```

In `_build_universe_members()`, keep:

```python
            selection_metric_name=config.ranking_type.lower(),
```

This already maps `MARKET_CAP` to `market_cap`.

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run pytest packages/cluefin-store/tests/test_ingestion.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/cluefin-store/src/cluefin_store/ingestion.py packages/cluefin-store/tests/test_ingestion.py
git commit -m "feat(store): 유니버스 이름을 시장 기준으로 생성"
```

## Task 2: KIS US Provider Ranking and OHLCV Normalization

**Files:**
- Create: `packages/cluefin-store/src/cluefin_store/kis_us.py`
- Create: `packages/cluefin-store/tests/test_kis_us_provider.py`

- [ ] **Step 1: Write the failing provider tests**

Create `packages/cluefin-store/tests/test_kis_us_provider.py`:

```python
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

    def get_stock_market_cap_rank(self, keyb: str, auth: str, excd: str, vol_rang: str, curr_gb: str = "0"):
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

    def get_stock_period_quote(self, auth: str, excd: str, symb: str, gubn: str, bymd: str, modp: str, keyb: str = ""):
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
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest packages/cluefin-store/tests/test_kis_us_provider.py -v
```

Expected: FAIL because `cluefin_store.kis_us` does not exist.

- [ ] **Step 3: Implement the KIS US provider**

Create `packages/cluefin-store/src/cluefin_store/kis_us.py`:

```python
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from cluefin_store.ingestion import RankedSymbol
from cluefin_store.models import DailyOhlcv, _quantize_price

US_EXCHANGES: tuple[str, ...] = ("NYS", "NAS", "AMS")


class KisUsMarketDataProvider:
    provider_name = "kis"

    def __init__(self, *, client: Any) -> None:
        self.client = client
        self.exchange_by_symbol: dict[str, str] = {}

    @classmethod
    def from_env(cls) -> "KisUsMarketDataProvider":
        from cluefin_openapi.client_factory import BrokerClientFactory

        return cls(client=BrokerClientFactory().create_kis())

    def fetch_ranked_symbols(
        self,
        *,
        ranking_type: str,
        market_country: str,
        duration: str,
        count: int,
    ) -> list[RankedSymbol]:
        if market_country.upper() != "US":
            raise ValueError("KisUsMarketDataProvider only supports market_country=US")
        if ranking_type.upper() != "MARKET_CAP":
            raise ValueError("KisUsMarketDataProvider only supports ranking_type=MARKET_CAP")

        rows: list[tuple[str, str, str, Decimal]] = []
        self.exchange_by_symbol.clear()
        for exchange in US_EXCHANGES:
            response = self.client.overseas_market_analysis.get_stock_market_cap_rank(
                keyb="",
                auth="",
                excd=exchange,
                vol_rang="0",
            )
            for item in getattr(response.body, "output2", []):
                symbol = str(getattr(item, "symb", "") or "").strip()
                metric = _decimal_or_none(getattr(item, "tomv_org", None)) or _decimal_or_none(getattr(item, "tomv", None))
                if not symbol or metric is None:
                    continue
                name = str(getattr(item, "name", "") or getattr(item, "ename", "") or symbol).strip()
                item_exchange = str(getattr(item, "excd", "") or exchange).strip() or exchange
                rows.append((symbol, name or symbol, item_exchange, metric))

        rows.sort(key=lambda row: row[3], reverse=True)
        selected = rows[:count]
        if not selected:
            raise ValueError("KIS US market-cap ranking returned no parseable rows")

        ranked: list[RankedSymbol] = []
        for index, (symbol, name, exchange, metric) in enumerate(selected, start=1):
            self.exchange_by_symbol[symbol] = exchange
            ranked.append(
                RankedSymbol(
                    rank=index,
                    symbol=symbol,
                    name=name,
                    selection_metric_value=metric,
                )
            )
        return ranked

    def fetch_daily_ohlcv(
        self,
        symbols: tuple[str, ...],
        start_date: date,
        end_date: date,
    ) -> dict[str, list[DailyOhlcv]]:
        result: dict[str, list[DailyOhlcv]] = {}
        for symbol in symbols:
            exchange = self.exchange_by_symbol.get(symbol)
            if not exchange:
                raise ValueError(f"KIS exchange code is missing for symbol={symbol}")
            response = self.client.overseas_basic_quote.get_stock_period_quote(
                auth="",
                excd=exchange,
                symb=symbol,
                gubn="0",
                bymd=end_date.strftime("%Y%m%d"),
                modp="1",
            )
            candles = [
                _parse_period_quote_candle(symbol, candle)
                for candle in getattr(response.body, "output2", [])
            ]
            result[symbol] = sorted(
                (candle for candle in candles if start_date <= candle.trade_date <= end_date),
                key=lambda candle: candle.trade_date,
            )
        return result


def _parse_period_quote_candle(symbol: str, candle: Any) -> DailyOhlcv:
    trade_date = datetime.strptime(str(getattr(candle, "xymd")), "%Y%m%d").date()
    open_price = _price(getattr(candle, "open"))
    high_price = _price(getattr(candle, "high"))
    low_price = _price(getattr(candle, "low"))
    close_price = _price(getattr(candle, "clos"))
    volume = int(Decimal(str(getattr(candle, "tvol"))).to_integral_value(rounding=ROUND_HALF_UP))
    trading_amount = _decimal_or_none(getattr(candle, "tamt", None))
    if trading_amount is None:
        trading_amount = close_price * Decimal(volume)
    return DailyOhlcv(
        trade_date=trade_date,
        provider=KisUsMarketDataProvider.provider_name,
        symbol=symbol,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=volume,
        trading_amount=_quantize_price(trading_amount),
        run_id=uuid4(),
        collected_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


def _price(value: Any) -> Decimal:
    parsed = _decimal_or_none(value)
    if parsed is None:
        raise ValueError(f"missing KIS price value: {value!r}")
    return _quantize_price(parsed)


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None
```

- [ ] **Step 4: Run provider tests**

Run:

```bash
uv run pytest packages/cluefin-store/tests/test_kis_us_provider.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/cluefin-store/src/cluefin_store/kis_us.py packages/cluefin-store/tests/test_kis_us_provider.py
git commit -m "feat(store): KIS 미국 시총 공급자 추가"
```

## Task 3: CLI Provider Selection

**Files:**
- Modify: `packages/cluefin-store/src/cluefin_store/cli.py`
- Modify: `packages/cluefin-store/tests/test_cli.py`
- Modify: `packages/cluefin-store/pyproject.toml`

- [ ] **Step 1: Write failing CLI tests**

Append this test to `packages/cluefin-store/tests/test_cli.py`:

```python
def test_backfill_top_kis_us_dry_run_prints_plan_without_connecting() -> None:
    result = CliRunner().invoke(
        cli,
        [
            "backfill-top",
            "--provider",
            "kis",
            "--market-country",
            "US",
            "--ranking-type",
            "MARKET_CAP",
            "--ranking-duration",
            "current",
            "--end-date",
            "2026-08-17",
            "--years",
            "1",
            "--count",
            "50",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert '"provider": "kis"' in result.output
    assert '"market_country": "US"' in result.output
    assert '"ranking_type": "MARKET_CAP"' in result.output
    assert '"universe_name": "us_market_cap_top50_current"' in result.output
```

- [ ] **Step 2: Run CLI tests to verify failure**

Run:

```bash
uv run pytest packages/cluefin-store/tests/test_cli.py -v
```

Expected: FAIL because `--provider kis` and `--market-country` are not accepted.

- [ ] **Step 3: Add `cluefin-openapi` dependency**

Modify `packages/cluefin-store/pyproject.toml` dependencies:

```toml
dependencies = [
    "click>=8.1.7",
    "clickhouse-connect>=0.10.0",
    "cluefin-openapi",
    "pydantic>=2.12.0,<3.0.0",
    "requests>=2.32.0",
]
```

- [ ] **Step 4: Extend CLI options and provider factory**

In `packages/cluefin-store/src/cluefin_store/cli.py`, add the import:

```python
from cluefin_store.kis_us import KisUsMarketDataProvider
```

Update the `backfill-top` options:

```python
@click.option("--provider", type=click.Choice(["toss", "kis"]), default="toss", show_default=True)
@click.option("--market-country", default="KR", show_default=True)
@click.option("--end-date", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
@click.option("--years", type=int, default=1, show_default=True)
@click.option("--count", type=int, default=50, show_default=True)
@click.option("--ranking-type", default="MARKET_TRADING_AMOUNT", show_default=True)
@click.option("--ranking-duration", default="1y", show_default=True)
@click.option("--warmup-calendar-days", type=int, default=420, show_default=True)
@click.option("--dry-run", is_flag=True, help="Print the backfill plan without connecting to providers or ClickHouse.")
```

Update the function signature and config:

```python
def backfill_top_command(
    provider: str,
    market_country: str,
    end_date,
    years: int,
    count: int,
    ranking_type: str,
    ranking_duration: str,
    warmup_calendar_days: int,
    dry_run: bool,
) -> None:
    config = TopBackfillConfig(
        end_date=date(end_date.year, end_date.month, end_date.day),
        years=years,
        count=count,
        ranking_type=ranking_type,
        ranking_duration=ranking_duration,
        market_country=market_country.upper(),
        warmup_calendar_days=warmup_calendar_days,
    )
```

Add `market_country` to the dry-run plan:

```python
        "market_country": config.market_country,
```

Replace provider construction with:

```python
    if provider == "toss":
        market_provider = TossMarketDataProvider.from_env()
    elif provider == "kis":
        market_provider = KisUsMarketDataProvider.from_env()
    else:
        raise click.ClickException(f"Unsupported provider: {provider}")
```

- [ ] **Step 5: Run CLI tests**

Run:

```bash
uv run pytest packages/cluefin-store/tests/test_cli.py -v
```

Expected: PASS, including the existing Toss dry-run test.

- [ ] **Step 6: Commit**

```bash
git add packages/cluefin-store/src/cluefin_store/cli.py packages/cluefin-store/tests/test_cli.py packages/cluefin-store/pyproject.toml
git commit -m "feat(store): KIS 미국 시총 백필 CLI 추가"
```

## Task 4: Full Local Verification

**Files:**
- No source edits expected.

- [ ] **Step 1: Run targeted package tests**

Run:

```bash
uv run pytest packages/cluefin-store/tests -v
```

Expected: PASS.

- [ ] **Step 2: Run fast workspace tests**

Run:

```bash
uv run pytest -m "not integration and not slow"
```

Expected: PASS or only pre-existing unrelated failures. If failures occur, inspect and fix any failure caused by this change.

- [ ] **Step 3: Check working tree**

Run:

```bash
git status --short
```

Expected: clean except intentionally uncommitted user files.

- [ ] **Step 4: Report usage command**

In the final report, include this command:

```bash
uv run cluefin-store backfill-top --provider kis --market-country US --ranking-type MARKET_CAP --ranking-duration current --end-date 2026-08-17 --years 1 --count 50
```

Mention that it uses KIS credentials from `.env` or environment variables and writes to the configured ClickHouse.
