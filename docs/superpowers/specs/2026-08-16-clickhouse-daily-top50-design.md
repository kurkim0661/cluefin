# ClickHouse Daily Top 50 Market Data Store Design

## Goal

Build a single-server ClickHouse store for daily Korean stock research. The first
production workflow stores the daily top 50 Korean stocks by trading amount and
all supporting daily data needed for analysis.

The store must support DART immediately, Toss Invest as a first-class market data
provider, and KIS as an additional provider. Kiwoom remains optional and is not
required for the initial workflow.

## Non-Goals

- No minute, tick, order-book, or realtime WebSocket storage in this phase.
- No order placement, order correction, order cancellation, or trading execution.
- No multi-node ClickHouse cluster.
- No TypeScript client changes in the initial implementation.
- No portfolio or account tables unless a later task explicitly adds them.

## Architecture

Add a new Python workspace package:

```text
packages/cluefin-store/
```

Responsibilities:

- Manage ClickHouse connection settings.
- Initialize database/schema objects.
- Run daily ingestion jobs.
- Normalize provider responses into common daily tables.
- Preserve raw provider payloads for replay and debugging.

The package exposes a CLI:

```bash
uv run cluefin-store init
uv run cluefin-store ingest daily-top50 --trade-date 20260816 --provider toss
uv run cluefin-store ingest daily-top50-backfill --lookback-days 30 --provider toss
uv run cluefin-store ingest daily-top50-backfill --from 20260716 --to 20260816 --provider toss
uv run cluefin-store ingest dart-disclosures --from 20260801 --to 20260816
uv run cluefin-store doctor
```

Development uses one local ClickHouse server:

```text
docker-compose.clickhouse.yml
```

Python uses `clickhouse-connect` for inserts and queries.

## Provider Strategy

Provider support is modular. Each provider adapter has one job: fetch provider
data and return normalized Python records.

Initial adapters:

- `dart`: uses the existing `cluefin-openapi` DART client and current credentials.
- `toss`: uses Toss Invest Open API for rankings, daily prices/candles, stock
  reference data, market calendar, market indicators, exchange rates, and daily
  supply-demand style data where available.
- `kis`: uses the existing `cluefin-openapi-cli` registry or Python client for
  compatible daily data.

Toss should be added as a reusable Python client under `packages/cluefin-openapi`
when practical, not hidden only inside `cluefin-store`. This keeps future CLI,
desk, and analysis tools on the same provider wrapper.

## Daily Workflow

The first workflow is `daily-top50`.

Inputs:

- `trade_date`
- `provider`, default `toss`
- `market_country`, default `KR`
- `limit`, fixed at `50` for the first implementation

Steps:

1. Create an `ingest_runs` row.
2. Fetch trading amount ranking top 50 for the trade date.
3. Store ranking rows in `market.daily_trading_amount_top50`.
4. Fetch daily OHLCV for all top 50 symbols.
5. Store daily candles in `market.daily_ohlcv`.
6. Fetch stock master details for all top 50 symbols.
7. Upsert stock reference rows in `market.stock_master`.
8. Fetch daily investor trading, short selling, credit, and lending data when
   available from the selected provider.
9. Store all raw provider payloads in `store.raw_api_events`.
10. Mark the ingest run as `success` or `failed`.

The workflow is append-friendly and idempotent by logical key. Re-running the
same trade date should not create duplicate analytical facts after `FINAL` or
latest-version selection.

## One-Month Backfill Workflow

The default historical load is `daily-top50-backfill`.

Inputs:

- `provider`, default `toss`
- `lookback_days`, default `30`
- Optional explicit `from` and `to` dates in `YYYYMMDD`
- `market_country`, default `KR`
- `limit`, fixed at `50` for the first implementation

Steps:

1. Resolve the date range. If explicit dates are not provided, use the last 30
   calendar days ending at the requested or current date.
2. Load the market calendar for `KR`.
3. Keep only open trading days.
4. Run `daily-top50` once per open trading day.
5. Continue past per-day optional endpoint failures, but record each failed day
   in `store.ingest_runs`.
6. Stop only when the provider cannot supply the core ranking or OHLCV data for
   a day after configured retries.

If a provider cannot query historical top 50 rankings for an arbitrary trade
date, the adapter must return a typed unsupported-capability error for that
date. The backfill command records the failed run and continues to the next
trading day. It must not silently substitute the current top 50 for historical
dates.

## Database Layout

Use three ClickHouse databases:

```text
store
market
dart
```

- `store`: ingestion metadata and raw API payloads.
- `market`: provider-normalized market facts and reference data.
- `dart`: disclosure and company event data.

## Store Tables

```sql
CREATE TABLE store.ingest_runs
(
    run_id UUID,
    provider LowCardinality(String),
    job_name LowCardinality(String),
    trade_date Nullable(Date),
    started_at DateTime64(3, 'Asia/Seoul'),
    finished_at Nullable(DateTime64(3, 'Asia/Seoul')),
    status LowCardinality(String),
    params_json String,
    error Nullable(String)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(started_at)
ORDER BY (job_name, provider, started_at, run_id);
```

```sql
CREATE TABLE store.raw_api_events
(
    run_id UUID,
    provider LowCardinality(String),
    endpoint String,
    request_hash FixedString(64),
    requested_at DateTime64(3, 'Asia/Seoul'),
    response_status UInt16,
    payload_json String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(requested_at)
ORDER BY (provider, endpoint, requested_at, request_hash);
```

## Market Tables

```sql
CREATE TABLE market.daily_trading_amount_top50
(
    trade_date Date,
    provider LowCardinality(String),
    market_country LowCardinality(String),
    ranking_type LowCardinality(String),
    rank UInt16,
    symbol String,
    name String,
    close_price Decimal(18, 4),
    change_amount Decimal(18, 4),
    change_rate Float64,
    trading_volume UInt64,
    trading_amount Decimal(24, 4),
    run_id UUID,
    collected_at DateTime64(3, 'Asia/Seoul')
)
ENGINE = ReplacingMergeTree(collected_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, provider, ranking_type, symbol);
```

```sql
CREATE TABLE market.daily_ohlcv
(
    trade_date Date,
    provider LowCardinality(String),
    symbol String,
    open Decimal(18, 4),
    high Decimal(18, 4),
    low Decimal(18, 4),
    close Decimal(18, 4),
    volume UInt64,
    trading_amount Decimal(24, 4),
    run_id UUID,
    collected_at DateTime64(3, 'Asia/Seoul')
)
ENGINE = ReplacingMergeTree(collected_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (symbol, provider, trade_date);
```

```sql
CREATE TABLE market.daily_investor_trading
(
    trade_date Date,
    provider LowCardinality(String),
    symbol String,
    investor_type LowCardinality(String),
    buy_amount Decimal(24, 4),
    sell_amount Decimal(24, 4),
    net_buy_amount Decimal(24, 4),
    buy_volume UInt64,
    sell_volume UInt64,
    net_buy_volume Int64,
    run_id UUID,
    collected_at DateTime64(3, 'Asia/Seoul')
)
ENGINE = ReplacingMergeTree(collected_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (symbol, provider, investor_type, trade_date);
```

```sql
CREATE TABLE market.daily_short_credit_lending
(
    trade_date Date,
    provider LowCardinality(String),
    symbol String,
    short_selling_amount Nullable(Decimal(24, 4)),
    short_selling_volume Nullable(UInt64),
    credit_balance_amount Nullable(Decimal(24, 4)),
    credit_balance_volume Nullable(UInt64),
    lending_balance_amount Nullable(Decimal(24, 4)),
    lending_balance_volume Nullable(UInt64),
    run_id UUID,
    collected_at DateTime64(3, 'Asia/Seoul')
)
ENGINE = ReplacingMergeTree(collected_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (symbol, provider, trade_date);
```

```sql
CREATE TABLE market.stock_master
(
    provider LowCardinality(String),
    symbol String,
    name String,
    market Nullable(LowCardinality(String)),
    market_country LowCardinality(String),
    currency Nullable(LowCardinality(String)),
    security_type Nullable(LowCardinality(String)),
    updated_at DateTime64(3, 'Asia/Seoul')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (provider, symbol);
```

```sql
CREATE TABLE market.market_calendar
(
    market_country LowCardinality(String),
    trade_date Date,
    is_open UInt8,
    reason Nullable(String),
    provider LowCardinality(String),
    updated_at DateTime64(3, 'Asia/Seoul')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (market_country, trade_date, provider);
```

```sql
CREATE TABLE market.exchange_rates
(
    trade_date Date,
    provider LowCardinality(String),
    base_currency LowCardinality(String),
    quote_currency LowCardinality(String),
    rate Decimal(18, 8),
    run_id UUID,
    collected_at DateTime64(3, 'Asia/Seoul')
)
ENGINE = ReplacingMergeTree(collected_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (base_currency, quote_currency, provider, trade_date);
```

## DART Tables

```sql
CREATE TABLE dart.daily_disclosures
(
    rcept_dt Date,
    corp_code String,
    stock_code Nullable(String),
    corp_name String,
    report_nm String,
    rcept_no String,
    flr_nm String,
    disclosure_type Nullable(String),
    raw_json String,
    run_id UUID,
    collected_at DateTime64(3, 'Asia/Seoul')
)
ENGINE = ReplacingMergeTree(collected_at)
PARTITION BY toYYYYMM(rcept_dt)
ORDER BY (rcept_dt, corp_code, rcept_no);
```

```sql
CREATE TABLE dart.symbol_corp_map
(
    symbol String,
    corp_code Nullable(String),
    corp_name Nullable(String),
    source LowCardinality(String),
    updated_at DateTime64(3, 'Asia/Seoul')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY symbol;
```

## Analysis Queries

Example: top 50 with daily momentum and investor flow.

```sql
WITH ohlcv_with_return AS
(
    SELECT
        trade_date,
        symbol,
        close,
        close / lagInFrame(close) OVER (
            PARTITION BY symbol
            ORDER BY trade_date
        ) - 1 AS daily_return
    FROM market.daily_ohlcv
    WHERE trade_date >= today() - 60
),
flow AS
(
    SELECT
        trade_date,
        symbol,
        sumIf(net_buy_amount, investor_type = 'foreign') AS foreign_net_buy,
        sumIf(net_buy_amount, investor_type = 'institution') AS institution_net_buy
    FROM market.daily_investor_trading
    WHERE trade_date >= today() - 30
    GROUP BY trade_date, symbol
)
SELECT
    r.trade_date,
    r.rank,
    r.symbol,
    r.name,
    r.trading_amount,
    o.close,
    o.daily_return,
    f.foreign_net_buy,
    f.institution_net_buy
FROM market.daily_trading_amount_top50 AS r
LEFT JOIN ohlcv_with_return AS o
    ON r.trade_date = o.trade_date
   AND r.symbol = o.symbol
LEFT JOIN flow AS f
    ON r.trade_date = f.trade_date
   AND r.symbol = f.symbol
WHERE r.trade_date >= today() - 30
ORDER BY r.trade_date DESC, r.rank ASC;
```

Example: top 50 symbols with same-day DART disclosures.

```sql
SELECT
    r.trade_date,
    r.rank,
    r.symbol,
    r.name,
    d.report_nm,
    d.rcept_no
FROM market.daily_trading_amount_top50 AS r
LEFT JOIN dart.symbol_corp_map AS m
    ON r.symbol = m.symbol
LEFT JOIN dart.daily_disclosures AS d
    ON r.trade_date = d.rcept_dt
   AND m.corp_code = d.corp_code
WHERE r.trade_date = {trade_date:Date}
ORDER BY r.rank ASC, d.rcept_no ASC;
```

## Error Handling

- Every ingestion creates an `ingest_runs` row.
- Provider API responses are saved in `raw_api_events` before normalization when
  possible.
- If one optional supporting endpoint fails, the job records the error and
  continues with the core ranking/OHLCV path.
- If the top 50 ranking fetch fails, the whole job fails.
- Secrets are never logged or stored.

## Testing

Unit tests use mocked provider clients and no network:

- DDL generation includes all expected databases and tables.
- Daily top 50 normalization maps provider payloads into stable records.
- Re-running the same trade date inserts rows with the same logical keys.
- Optional endpoint failures do not fail core ranking/OHLCV ingestion.
- CLI parameter validation rejects invalid dates and unsupported providers.

Integration tests are marked `integration` and require a local ClickHouse server
plus real provider credentials.

## Open Questions Resolved

- Granularity is daily only.
- Initial universe is trading amount top 50.
- Toss is supported as a first-class market data provider.
- DART is used for disclosure and company-event joins.
- Account and order APIs are excluded from the initial implementation.
