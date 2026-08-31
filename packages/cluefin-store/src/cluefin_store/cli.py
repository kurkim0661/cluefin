from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from uuid import uuid4

import click

from cluefin_store.analysis import PatternAnalysisConfig, pattern_collection_plan
from cluefin_store.db import ClickHouseSettings, ClickHouseStore
from cluefin_store.indicators import (
    INDICATOR_CATALOG,
    catalog_summary,
    collect_market_indicators,
    default_indicator_providers,
    import_indicator_csv,
)
from cluefin_store.ingestion import TopBackfillConfig, backfill_top_ranked
from cluefin_store.real_estate import REAL_ESTATE_CATALOG, collect_real_estate
from cluefin_store.schema import SCHEMA_STATEMENTS
from cluefin_store.sentiment import GoogleNewsRssSearchProvider, build_sentiment_items, sentiment_query
from cluefin_store.toss import TossMarketDataProvider
from cluefin_store.toss_us import TossUsMarketCapProvider


@click.group()
def cli() -> None:
    """ClickHouse-backed daily market data store commands."""


@cli.command()
@click.option("--dry-run", is_flag=True, help="Print connection settings without connecting.")
def doctor(dry_run: bool) -> None:
    settings = ClickHouseSettings.from_env()
    click.echo(f"ClickHouse host: {settings.host}")
    click.echo(f"ClickHouse port: {settings.port}")
    click.echo(f"ClickHouse user: {settings.username}")
    click.echo(f"ClickHouse secure: {str(settings.secure).lower()}")
    click.echo(f"Dry run: {str(dry_run).lower()}")
    if not dry_run:
        ClickHouseStore(settings=settings).client().command("SELECT 1")
        click.echo("Connection: ok")


@cli.command(name="init")
@click.option("--dry-run", is_flag=True, help="Print schema DDL without applying it.")
def init_command(dry_run: bool) -> None:
    if dry_run:
        click.echo(";\n".join(statement.strip() for statement in SCHEMA_STATEMENTS))
        return

    applied = ClickHouseStore().apply_schema()
    click.echo(f"Applied schema statements: {applied}")


@cli.command(name="update-indicators")
@click.option("--start-date", type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--end-date", type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option(
    "--provider",
    "provider_names",
    multiple=True,
    type=click.Choice(
        ["all", "fred", "defillama", "coinmetrics", "coingecko", "binance", "customs", "ecos", "dart", "licensed"]
    ),
    default=("all",),
    show_default=True,
)
@click.option("--strict", is_flag=True, help="Stop immediately when one provider fails.")
@click.option("--dry-run", is_flag=True, help="Print catalog and collection plan without network or database access.")
def update_indicators_command(
    start_date, end_date, provider_names: tuple[str, ...], strict: bool, dry_run: bool
) -> None:
    """Collect macro, Korean-market, equity, and crypto indicators."""
    resolved_end = date(end_date.year, end_date.month, end_date.day) if end_date else date.today()
    resolved_start = (
        date(start_date.year, start_date.month, start_date.day) if start_date else resolved_end - timedelta(days=400)
    )
    if resolved_start > resolved_end:
        raise click.BadParameter("start-date must not be after end-date", param_hint="--start-date")

    store = None if dry_run else ClickHouseStore()
    available_providers = {provider.provider_name: provider for provider in default_indicator_providers(store=store)}
    selected_names = tuple(available_providers) if "all" in provider_names else tuple(dict.fromkeys(provider_names))
    plan = {
        "start_date": resolved_start.isoformat(),
        "end_date": resolved_end.isoformat(),
        "providers": list(selected_names),
        "catalog": catalog_summary(),
        "tables": ["market.indicator_definitions", "market.indicator_observations"],
        "strict": strict,
    }
    if dry_run:
        click.echo(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        return

    assert store is not None
    store.apply_schema()
    summary = collect_market_indicators(
        store=store,
        providers=tuple(available_providers[name] for name in selected_names),
        start_date=resolved_start,
        end_date=resolved_end,
        run_id=uuid4(),
        collected_at=datetime.now(),
        catalog=INDICATOR_CATALOG,
        strict=strict,
    )
    click.echo(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


@cli.command(name="update-real-estate")
@click.option("--start-date", type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--end-date", type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option(
    "--metric",
    "metric_ids",
    multiple=True,
    type=click.Choice([spec.metric_id for spec in REAL_ESTATE_CATALOG]),
    help="Collect only these metrics. Repeat the flag for several.",
)
@click.option("--strict", is_flag=True, help="Fail the run when any series errors.")
@click.option("--dry-run", is_flag=True, help="Print the collection plan without network or database access.")
def update_real_estate_command(start_date, end_date, metric_ids: tuple[str, ...], strict: bool, dry_run: bool) -> None:
    """Collect capital-area housing price, supply, and land indicators."""
    resolved_end = date(end_date.year, end_date.month, end_date.day) if end_date else date.today()
    resolved_start = (
        date(start_date.year, start_date.month, start_date.day) if start_date else resolved_end - timedelta(days=365)
    )
    if resolved_start > resolved_end:
        raise click.BadParameter("start-date must not be after end-date", param_hint="--start-date")

    summary = collect_real_estate(
        None if dry_run else ClickHouseStore(),
        resolved_start,
        resolved_end,
        metrics=metric_ids or None,
        strict=strict,
        dry_run=dry_run,
    )
    click.echo(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


@cli.command(name="import-indicators")
@click.option("--file", "input_file", type=click.Path(exists=True, dir_okay=False, path_type=str), required=True)
@click.option("--dry-run", is_flag=True, help="Validate and report the file without writing ClickHouse rows.")
def import_indicators_command(input_file: str, dry_run: bool) -> None:
    """Import normalized licensed or manually supplied indicator observations."""
    with open(input_file, encoding="utf-8-sig") as handle:
        csv_text = handle.read()
    if dry_run:
        summary = import_indicator_csv(
            store=_DryRunIndicatorStore(),
            csv_text=csv_text,
            run_id=uuid4(),
            collected_at=datetime.now(),
        )
        summary["file"] = input_file
        click.echo(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return
    store = ClickHouseStore()
    store.apply_schema()
    summary = import_indicator_csv(
        store=store,
        csv_text=csv_text,
        run_id=uuid4(),
        collected_at=datetime.now(),
    )
    click.echo(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


class _DryRunIndicatorStore:
    def insert_records(self, _table: str, records) -> int:
        return len(records)


@cli.command(name="pattern-plan")
@click.option("--start-date", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
@click.option("--end-date", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
@click.option("--symbol", multiple=True, required=True, help="Stock symbol to include. Can be repeated.")
@click.option("--warmup-calendar-days", type=int, default=420, show_default=True)
def pattern_plan(start_date, end_date, symbol: tuple[str, ...], warmup_calendar_days: int) -> None:
    """Print the data collection window required for daily pattern analysis."""
    plan = pattern_collection_plan(
        symbols=symbol,
        target_start=date(start_date.year, start_date.month, start_date.day),
        target_end=date(end_date.year, end_date.month, end_date.day),
        config=PatternAnalysisConfig(warmup_calendar_days=warmup_calendar_days),
    )
    click.echo(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))


@cli.command(name="backfill-top")
@click.option("--provider", type=click.Choice(["toss", "toss_us"]), default="toss", show_default=True)
@click.option("--market-country", type=click.Choice(["KR", "US"]), default="KR", show_default=True)
@click.option("--end-date", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
@click.option("--years", type=int, default=1, show_default=True)
@click.option("--count", type=int, default=50, show_default=True)
@click.option("--ranking-type", default="MARKET_TRADING_AMOUNT", show_default=True)
@click.option("--ranking-duration", default="1y", show_default=True)
@click.option("--warmup-calendar-days", type=int, default=420, show_default=True)
@click.option("--dry-run", is_flag=True, help="Print the backfill plan without connecting to Toss or ClickHouse.")
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
    """Backfill ClickHouse with top-ranked Toss market data and pattern analytics."""
    config = TopBackfillConfig(
        end_date=date(end_date.year, end_date.month, end_date.day),
        years=years,
        count=count,
        ranking_type=ranking_type,
        ranking_duration=ranking_duration,
        market_country=market_country,
        warmup_calendar_days=warmup_calendar_days,
    )
    plan = {
        "provider": provider,
        "market_country": config.market_country,
        "ranking_type": config.ranking_type,
        "ranking_duration": config.ranking_duration,
        "count": config.count,
        "target_start": config.target_start.isoformat(),
        "target_end": config.end_date.isoformat(),
        "collection_start": (config.target_start - timedelta(days=config.warmup_calendar_days)).isoformat(),
        "collection_end": (config.end_date + timedelta(days=max(config.outcome_horizons, default=0))).isoformat(),
        "universe_name": config.resolved_universe_name,
        "tables": [
            "market.daily_universe_members",
            "market.daily_ohlcv",
            "market.daily_technical_features",
            "market.daily_volume_profile_levels",
            "market.daily_pattern_events",
            "market.daily_pattern_outcomes",
        ],
    }
    if dry_run:
        click.echo(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        return

    market_provider = TossMarketDataProvider.from_env() if provider == "toss" else TossUsMarketCapProvider.from_env()
    store = ClickHouseStore()
    store.apply_schema()
    summary = backfill_top_ranked(
        provider=market_provider,
        store=store,
        config=config,
        run_id=uuid4(),
        collected_at=datetime.now(),
    )
    click.echo(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


@cli.command(name="update-sentiment")
@click.option("--symbol", multiple=True, help="Stock symbol to update. Defaults to the latest universe.")
@click.option("--limit", type=int, default=5, show_default=True, help="News search results per symbol.")
@click.option("--dry-run", is_flag=True, help="Print the update plan without fetching news or writing rows.")
def update_sentiment_command(symbol: tuple[str, ...], limit: int, dry_run: bool) -> None:
    """Update symbol sentiment from latest web-search news headlines."""
    store = ClickHouseStore()
    symbols = _sentiment_symbols(store, symbol)
    plan = {
        "provider": GoogleNewsRssSearchProvider.provider_name,
        "symbols": [
            {"symbol": item["symbol"], "name": item["name"], "query": sentiment_query(item["symbol"], item["name"])}
            for item in symbols
        ],
        "limit": limit,
        "table": "market.symbol_sentiment_items",
    }
    if dry_run:
        click.echo(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        return

    store.apply_schema()
    provider = GoogleNewsRssSearchProvider()
    run_id = uuid4()
    collected_at = datetime.now()
    inserted = 0
    for item in symbols:
        query = sentiment_query(item["symbol"], item["name"])
        results = provider.search(query, limit=limit)
        rows = build_sentiment_items(
            symbol=item["symbol"],
            name=item["name"],
            provider_name=provider.provider_name,
            results=results,
            run_id=run_id,
            collected_at=collected_at,
        )
        inserted += store.insert_records("market.symbol_sentiment_items", rows)
    click.echo(json.dumps({"symbols": len(symbols), "inserted": inserted}, ensure_ascii=False, sort_keys=True))


def _sentiment_symbols(store: ClickHouseStore, symbols: tuple[str, ...]) -> list[dict[str, str]]:
    if symbols:
        return [{"symbol": item, "name": item} for item in symbols]
    rows = store.client().query(
        """
        SELECT symbol, anyLast(name) AS name
        FROM market.daily_universe_members
        WHERE trade_date = (SELECT max(trade_date) FROM market.daily_universe_members)
        GROUP BY symbol
        ORDER BY symbol ASC
        """
    )
    return [{"symbol": row[0], "name": row[1] or row[0]} for row in rows.result_rows]
