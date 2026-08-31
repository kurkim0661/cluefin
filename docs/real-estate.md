# Capital-area real estate

Cluefin stores Korean housing, supply, and land series as one long-format fact table so the
dashboard can pivot them by any dimension instead of shipping fixed charts.

- `market.real_estate_metrics`: metric catalog (name, unit, deal type, description, source).
- `market.real_estate_observations`: one numeric observation per period × metric × region ×
  property type × deal type, with `week_start` pre-computed for weekly bucketing.

## Source

Every series comes from the Bank of Korea **ECOS** relay, which republishes Korea Real Estate
Board, KB, and national statistics **without an API key**. `BOK_ECOS_API_KEY` only raises the
page size from 10 to 1000 rows.

| Metric | ECOS table | Dimensions |
|---|---|---|
| 주택 매매/전세/월세가격지수 | 901Y144 / 901Y145 / 901Y146 | 주택유형 4 × 지역 6 |
| 〃 장기 (`*_long`) | 901Y093 / 901Y094 / 901Y095 | 주택유형 4 × 지역 6 |
| 아파트 실거래가격지수 | 901Y089 | 지역 11 (서울 5개 권역 포함) |
| 미분양 주택 | 901Y074 | 지역 5 |
| 주택건설 인허가실적 | 901Y105 | 지역 4 |
| 지가변동률 | 901Y064 | 지역 4 |

The Korea Real Estate Board's own R-ONE OpenAPI serves weekly apartment tables, but without a
registered key it caps every response at five rows and ignores `pIndex`, so it cannot backfill.
Monthly ECOS series are therefore the collected grain; the dashboard buckets them by week,
month, or quarter.

## Commands

```bash
uv run cluefin-store update-real-estate --start-date 2024-08-01 --end-date 2026-08-31 --dry-run
uv run cluefin-store update-real-estate --start-date 2024-08-01 --end-date 2026-08-31
uv run cluefin-store update-real-estate --metric unsold_housing --metric housing_permits
```

The current price-index tables only start at 2021-06 because Korea Real Estate Board rebased them
to 2026.01. The pre-rebase tables reach back to 2003-11 (월세: 2015-06) and are collected as
separate `*_long` metrics. Their base month differs, so the two generations are **not** spliced
into one series; use the dashboard's `rebase` transform to compare their shapes over ten years.

Collect at least two years even when analysing one: year-over-year views need the prior year as
a comparison base. Housing statistics are revised, and `ReplacingMergeTree(collected_at)` keeps
the newest row per key, so overlapping windows are safe to re-run.

## Dashboard contract

`GET /api/real-estate/meta` returns the metric catalog, the distinct value list per dimension,
coverage, and the built-in templates.

`POST /api/real-estate/query` accepts:

```json
{
  "metric_ids": ["house_jeonse_price_index", "house_sale_price_index"],
  "series_dimension": "region",
  "bucket": "month",
  "aggregation": "avg",
  "transform": "ratio",
  "filters": {"region": ["서울", "경기"], "property_type": ["아파트"]},
  "start_date": "2025-01-01",
  "end_date": "2026-08-31"
}
```

- `series_dimension`: `metric_id`, `region`, `region_tier`, `property_type`, `deal_type`
- `bucket`: `week`, `month`, `quarter`
- `aggregation`: `avg`, `sum`, `max`, `min`, `last`
- `transform`: `raw`, `change`, `yoy`, `rebase`, `ratio` (`ratio` requires exactly two metrics
  and divides the first by the second)

Filter values are bound as query parameters, never string-interpolated.

## Verification

```sql
SELECT metric_id, count(), countDistinct(region), min(period), max(period)
FROM market.real_estate_observations FINAL
GROUP BY metric_id
ORDER BY metric_id;
```
