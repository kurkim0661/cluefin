# Market indicator pipeline

Cluefin normalizes macro, Korean-market, equity, and crypto context into two ClickHouse tables:

- `market.indicator_definitions`: names, domains, units, interpretation, source, frequency, importance, and availability.
- `market.indicator_observations`: one normalized numeric observation per indicator and period, with run provenance.

The catalog deliberately includes unavailable metrics. `public`, `api_key`, `licensed`, and `derived` distinguish a broken collector from data that needs credentials or a commercial contract. The web dashboard exposes the same state instead of silently showing stale or fabricated values.

## Sources

| Provider | Data | Access |
|---|---|---|
| FRED | Rates, real yields, curves, inflation, growth, labor, dollar, credit, liquidity, commodities | Public |
| DefiLlama | Stablecoin supply, DeFi TVL, fees, and protocol revenue | Public |
| Coin Metrics Community | BTC/ETH/XRP price, MVRV, activity, transactions, supply, and BTC hash rate | Public tier |
| CoinGecko | Total crypto market cap, volume, per-coin dominance, and PAXG gold-price proxy | Public tier |
| Binance Futures | Per-symbol funding-rate and open-interest history (BTC, ETH, XRP) | Public |
| ECOS | Bank of Korea policy rate and total/semiconductor export-value indexes | Public sample pagination; optional `BOK_ECOS_API_KEY` |
| Open DART | Latest-universe revenue/profit growth breadth, median ROE, and median debt ratio | `DART_AUTH_KEY` |
| Korea Customs | First-20-days export YoY from official monthly press releases | Public |
| Licensed feeds | EPS revisions, forward valuation, ETF flows, labeled exchange flows, SOPR, LTH supply, liquidations, token unlocks | Vendor contract/PAT |

The Fed net-liquidity series is an explicitly labeled proxy:

```text
WALCL / 1000 - WTREGEN / 1000 - RRPONTSYD
```

Realized price is derived as `PriceUSD / CapMVRVCur` and NUPL as `1 - 1/MVRV` for BTC, ETH, and XRP alike; ETH also derives 30-day supply growth and active-addresses-per-transaction. Derived values retain their formula and asset in `metadata_json`.

Coin Metrics is keyed by `<asset>:<metric>`, Binance by `<symbol>:<field>`, and CoinGecko dominance by `market_cap_percentage.<coin>`, so adding another asset means adding catalog rows only. A dominance series missing from the CoinGecko response is reported per indicator instead of dropping the whole batch.

## Commands

Inspect the full plan without network or database writes:

```bash
uv run cluefin-store update-indicators \
  --start-date 2025-07-01 \
  --end-date 2026-08-26 \
  --dry-run
```

Initialize and backfill public sources:

```bash
uv run cluefin-store init
uv run cluefin-store update-indicators \
  --start-date 2025-07-01 \
  --end-date 2026-08-26 \
  --provider fred \
  --provider defillama \
  --provider coinmetrics \
  --provider coingecko \
  --provider binance \
  --provider ecos
```

Run DART without printing credentials:

```bash
set -a
source .env
set +a
uv run cluefin-store update-indicators \
  --start-date 2025-01-01 \
  --end-date 2026-08-26 \
  --provider dart
```

Use `--strict` in scheduled jobs when a partial provider result should fail the run. Without it, successful providers are preserved and provider errors are returned in the JSON summary.

Licensed feeds can use the same storage contract without a custom database loader. Export `indicator_id,period,value` and optional `provider,metadata_json` columns, then validate and import:

```bash
uv run cluefin-store import-indicators --file vendor-indicators.csv --dry-run
uv run cluefin-store import-indicators --file vendor-indicators.csv
```

## Derived tables

`market.market_calendar`, `market.stock_master`, and `market.exchange_rates` have no collector of
their own — they are rebuilt from rows already in ClickHouse, so they cost nothing to regenerate:

```bash
uv run cluefin-store derive-tables --dry-run
uv run cluefin-store derive-tables
uv run cluefin-store derive-tables --table stock_master
```

- **market_calendar** ← distinct `daily_ohlcv` trade dates. `daily_ohlcv` has no country column, so
  the provider decides it (`toss`→KR, `toss_us`→US, overridden by `daily_universe_members`); two
  providers covering one country collapse into one row per day. Only trading days are written.
- **stock_master** ← latest `daily_universe_members` row per (provider, symbol) for the name and
  country; `currency` follows the country. `market` and `security_type` stay NULL because the Toss
  ranking response does not carry them — an empty column is better than an invented one.
- **exchange_rates** ← `indicator_observations` rows listed in `EXCHANGE_RATE_INDICATORS`
  (currently `usd_krw` → USD/KRW). Add a tuple there to publish another pair.

Re-run this after any `backfill-top` or `update-indicators` job that extends the source rows.

## VWAP is a 20-day rolling average

`daily_technical_features.vwap` is Σ(amount)/Σ(volume) over the trailing 20 sessions
(`patterns.VWAP_PERIOD`), not a single day's value. A one-day VWAP is amount ÷ volume, and the Toss
provider synthesises `daily_ohlcv.trading_amount` as `close × volume` (`toss.py`), which made the
column an exact copy of `close` — useless as an indicator, and it silently starved every paper
strategy that counted a vwap vote (agreement never reached 3 of 3). The rolling window restores a
real signal: with the same data, `close` now sits above vwap on 13.6k days and below on 10.3k.
The first 19 sessions per symbol are NULL because the window is not full yet.

## Suggested schedule

- Daily 07:30 KST: FRED, DefiLlama, Coin Metrics, CoinGecko; use a 14-day lookback to absorb revisions.
- Daily 07:45 KST: Binance futures and ECOS. Monthly and event series are safely upserted by period.
- Weekly after DART filing season: DART aggregate fundamentals for the latest ranked universe.
- Once after schema changes: a 400-day backfill to give cards enough history for trend lines.
- After any of the above: `derive-tables`, so the calendar, master, and FX tables track the new rows.

ClickHouse uses `ReplacingMergeTree(collected_at)`, so repeated overlapping windows are expected. Query with `FINAL` when checking canonical counts.

## Verification

```sql
SELECT
    provider,
    count(),
    countDistinct(indicator_id),
    min(period),
    max(period)
FROM market.indicator_observations FINAL
GROUP BY provider
ORDER BY provider;
```

```sql
SELECT
    d.availability,
    count() AS catalog_count,
    countIf(o.indicator_id != '') AS observed_count
FROM market.indicator_definitions FINAL AS d
LEFT JOIN
(
    SELECT DISTINCT indicator_id
    FROM market.indicator_observations FINAL
) AS o USING indicator_id
GROUP BY d.availability;
```

## Dashboard contract

`GET /api/market-pulse` returns:

- a conservative market-regime summary;
- source coverage and freshness;
- rate/liquidity, growth/prices, Korea, equity, and crypto section states;
- latest values, prior-period changes, 180-point chart history, source links, examples, and interpretation caveats;
- missing credentialed and licensed connections.

The regime score is explanatory, not a trading recommendation. Context-only indicators such as the yield curve, MVRV, VIX, and futures positioning do not receive a directional vote.

## Toss US market-cap universe

Toss exposes US candles and trading rankings but does not expose `MARKET_CAP`. Cluefin therefore ranks the public Nasdaq screener by market cap and then uses Toss for Korean display names and all OHLCV data. The source boundary is stored as `provider=toss_us`.

```bash
set -a
source .env
set +a
uv run cluefin-store backfill-top \
  --provider toss_us \
  --market-country US \
  --ranking-type MARKET_CAP \
  --end-date 2026-08-26 \
  --years 1 \
  --count 50
```
