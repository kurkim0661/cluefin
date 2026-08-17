from cluefin_store.schema import SCHEMA_STATEMENTS, table_names


def test_schema_includes_core_databases_and_tables() -> None:
    sql = "\n".join(SCHEMA_STATEMENTS)

    assert "CREATE DATABASE IF NOT EXISTS store" in sql
    assert "CREATE DATABASE IF NOT EXISTS market" in sql
    assert "CREATE DATABASE IF NOT EXISTS dart" in sql
    assert "CREATE DATABASE IF NOT EXISTS portfolio" in sql
    assert "CREATE TABLE IF NOT EXISTS store.ingest_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS market.daily_universe_members" in sql
    assert "vwap Nullable(Decimal(18, 4))" in sql
    assert "ema_20 Nullable(Decimal(18, 4))" in sql
    assert "rsi_14 Nullable(Float64)" in sql
    assert "rsi_divergence LowCardinality(String)" in sql
    assert "CREATE TABLE IF NOT EXISTS market.symbol_sentiment_items" in sql
    assert "CREATE VIEW IF NOT EXISTS market.symbol_sentiment_summary" in sql
    assert "CREATE TABLE IF NOT EXISTS market.daily_pattern_events" in sql
    assert "CREATE TABLE IF NOT EXISTS market.daily_pattern_outcomes" in sql
    assert "CREATE VIEW IF NOT EXISTS market.pattern_performance_summary" in sql
    assert "CREATE TABLE IF NOT EXISTS portfolio.paper_accounts" in sql
    assert "CREATE TABLE IF NOT EXISTS portfolio.paper_strategies" in sql
    assert "CREATE TABLE IF NOT EXISTS portfolio.paper_backtest_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS portfolio.paper_backtest_daily_equity" in sql
    assert "CREATE TABLE IF NOT EXISTS portfolio.paper_backtest_trades" in sql


def test_table_names_are_extracted_from_schema() -> None:
    assert "market.daily_ohlcv" in table_names()
    assert "market.daily_volume_profile_levels" in table_names()
    assert "portfolio.paper_backtest_runs" in table_names()
