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
| Coin Metrics Community | BTC/ETH price, MVRV, activity, transactions, supply, and BTC hash rate | Public tier |
| CoinGecko | Total crypto market cap, volume, BTC dominance, and PAXG gold-price proxy | Public tier |
| Binance Futures | BTC funding-rate and open-interest history | Public |
| ECOS | Bank of Korea policy rate and total/semiconductor export-value indexes | Public sample pagination; optional `BOK_ECOS_API_KEY` |
| Open DART | Latest-universe revenue/profit growth breadth, median ROE, and median debt ratio | `DART_AUTH_KEY` |
| Korea Customs | First-20-days export YoY from official monthly press releases | Public |
| Licensed feeds | EPS revisions, forward valuation, ETF flows, labeled exchange flows, SOPR, LTH supply, liquidations, token unlocks | Vendor contract/PAT |

The Fed net-liquidity series is an explicitly labeled proxy:

```text
WALCL / 1000 - WTREGEN / 1000 - RRPONTSYD
```

Bitcoin realized price is derived as `PriceUSD / CapMVRVCur`, NUPL as `1 - 1/MVRV`, and ETH 30-day supply growth from daily supply. Derived values retain their formula in `metadata_json`.

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

## Suggested schedule

- Daily 07:30 KST: FRED, DefiLlama, Coin Metrics, CoinGecko; use a 14-day lookback to absorb revisions.
- Daily 07:45 KST: Binance futures and ECOS. Monthly and event series are safely upserted by period.
- Weekly after DART filing season: DART aggregate fundamentals for the latest ranked universe.
- Once after schema changes: a 400-day backfill to give cards enough history for trend lines.

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
