# US Market Cap Top 50 ClickHouse Backfill Design

## Goal

Extend `cluefin-store backfill-top` so it can load the current US-stock top 50
universe by market capitalization into ClickHouse, then run the existing daily
OHLCV, technical feature, volume profile, pattern event, and pattern outcome
pipeline for that universe.

The workflow should use live KIS data at run time. The repository should not
commit a static top-50 symbol list.

## Non-Goals

- No TypeScript client changes.
- No Kiwoom overseas support changes.
- No realtime, minute, tick, or WebSocket storage.
- No new ClickHouse tables unless an implementation test proves the existing
  daily tables cannot represent the data.
- No automatic scheduled job.

## Architecture

Add a KIS-backed provider adapter inside `packages/cluefin-store` that satisfies
the existing `TopRankedProvider` protocol:

```text
packages/cluefin-store/src/cluefin_store/kis_us.py
```

`cluefin-store` will add a workspace dependency on `cluefin-openapi` and create
KIS clients through `BrokerClientFactory.from_env()` semantics already used by
the repo. Credentials remain in `.env` or the real environment and are never
printed.

The existing `backfill_top_ranked()` service remains the orchestration point. It
already knows how to:

- store `market.daily_universe_members`;
- fetch OHLCV through the provider protocol;
- build technical features and pattern analytics;
- insert the generated table rows.

The KIS adapter only normalizes KIS responses into `RankedSymbol` and
`DailyOhlcv`.

## CLI

Extend the existing command rather than adding a parallel command:

```bash
uv run cluefin-store backfill-top \
  --provider kis \
  --market-country US \
  --ranking-type MARKET_CAP \
  --end-date 2026-08-17 \
  --years 1 \
  --count 50
```

Defaults stay backward compatible for Toss/KR:

- `--provider` default: `toss`
- `--market-country` default: `KR`
- `--ranking-type` default: `MARKET_TRADING_AMOUNT`
- `--ranking-duration` default: `1y`

For KIS/US, the command resolves the default universe name to:

```text
us_market_cap_top50_current
```

The command still supports `--dry-run`; dry runs must not create a KIS client or
connect to ClickHouse.

## Ranking Data Flow

The adapter fetches KIS overseas market-cap rankings for the US exchanges:

- `NYS`
- `NAS`
- `AMS`

It normalizes rows from `get_stock_market_cap_rank()`:

- `symbol`: `symb`
- `name`: `name` or `ename` or symbol
- `selection_metric_value`: parsed market cap from `tomv_org` when available,
  otherwise `tomv`
- `rank`: recomputed after merging all exchanges and sorting by market cap
  descending

The command stores the merged top `count` rows in
`market.daily_universe_members` with:

- `market_country="US"`
- `universe_type="ranking"`
- `selection_metric_name="market_cap"`
- `provider="kis"`

## OHLCV Data Flow

For each selected symbol, the adapter fetches daily KIS overseas period prices
using the symbol exchange code discovered from the ranking row.

Each KIS candle becomes a `DailyOhlcv`:

- `trade_date`: parsed provider date
- `open`, `high`, `low`, `close`: Decimal prices quantized like existing rows
- `volume`: integer volume
- `trading_amount`: close price times volume when KIS does not provide a direct
  daily amount field in the selected response
- `provider="kis"`

Returned candles are filtered to the requested collection window and sorted by
date. Missing symbols should not abort the whole run unless every symbol fails.
The provider should report enough context in exceptions to identify the failed
symbol and exchange without exposing secrets.

## Error Handling

- If KIS credentials are missing, the command exits with the existing factory
  error message.
- If one exchange ranking call fails, the command fails because the top-50 set
  would be incomplete.
- If a ranked symbol has no parseable market cap, it is skipped.
- If all market-cap rows are skipped, the command fails before writing.
- If one symbol has no OHLCV rows, it is omitted from downstream analysis but
  remains in the universe membership rows so the ranking snapshot is preserved.

## Testing

Unit tests use fake KIS clients and no network.

Add focused tests for:

- KIS ranking merge across NYS/NAS/AMS and global top-50 sorting.
- market-cap parsing from `tomv_org` and fallback to `tomv`.
- KIS period-price normalization into `DailyOhlcv`.
- CLI dry-run for `--provider kis --market-country US --ranking-type MARKET_CAP`
  including the expected universe name and tables.
- Existing Toss CLI defaults remaining unchanged.

Optional integration use is manual and follows the repo testing policy:

```bash
uv run pytest -m integration
```

The normal local verification remains:

```bash
uv run pytest -m "not integration and not slow"
```
