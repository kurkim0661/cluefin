from click.testing import CliRunner

from cluefin_store.cli import cli


def test_doctor_dry_run_prints_connection_settings() -> None:
    result = CliRunner().invoke(cli, ["doctor", "--dry-run"])

    assert result.exit_code == 0
    assert "ClickHouse host:" in result.output
    assert "Dry run: true" in result.output


def test_init_dry_run_prints_schema_without_connecting() -> None:
    result = CliRunner().invoke(cli, ["init", "--dry-run"])

    assert result.exit_code == 0
    assert "CREATE DATABASE IF NOT EXISTS store" in result.output
    assert "CREATE TABLE IF NOT EXISTS market.daily_universe_members" in result.output


def test_pattern_plan_prints_required_ohlcv_window() -> None:
    result = CliRunner().invoke(
        cli,
        [
            "pattern-plan",
            "--start-date",
            "2026-08-01",
            "--end-date",
            "2026-08-31",
            "--symbol",
            "005930",
        ],
    )

    assert result.exit_code == 0
    assert '"required_data": "daily_ohlcv"' in result.output
    assert '"target_start": "2026-08-01"' in result.output
    assert '"tables"' in result.output


def test_backfill_top_dry_run_prints_plan() -> None:
    result = CliRunner().invoke(
        cli,
        [
            "backfill-top",
            "--provider",
            "toss",
            "--end-date",
            "2026-08-16",
            "--years",
            "1",
            "--count",
            "50",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert '"provider": "toss"' in result.output
    assert '"ranking_type": "MARKET_TRADING_AMOUNT"' in result.output
    assert '"target_start": "2025-08-16"' in result.output
    assert '"universe_name": "kr_market_trading_amount_top50_1y"' in result.output


def test_toss_us_market_cap_dry_run_prints_us_universe() -> None:
    result = CliRunner().invoke(
        cli,
        [
            "backfill-top",
            "--provider",
            "toss_us",
            "--market-country",
            "US",
            "--ranking-type",
            "MARKET_CAP",
            "--end-date",
            "2026-08-26",
            "--count",
            "50",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert '"provider": "toss_us"' in result.output
    assert '"universe_name": "us_market_cap_top50_current"' in result.output


def test_update_sentiment_dry_run_prints_web_search_plan() -> None:
    result = CliRunner().invoke(
        cli,
        [
            "update-sentiment",
            "--symbol",
            "005930",
            "--limit",
            "3",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert '"provider": "google_news_rss"' in result.output
    assert '"table": "market.symbol_sentiment_items"' in result.output
    assert "005930 주식 실적 수주 목표가" in result.output


def test_update_indicators_dry_run_prints_catalog_without_network() -> None:
    result = CliRunner().invoke(
        cli,
        [
            "update-indicators",
            "--start-date",
            "2026-08-01",
            "--end-date",
            "2026-08-26",
            "--provider",
            "fred",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert '"providers": [' in result.output
    assert '"fred"' in result.output
    assert '"market.indicator_observations"' in result.output
    assert '"licensed"' in result.output


def test_import_indicators_dry_run_validates_normalized_csv(tmp_path) -> None:
    source = tmp_path / "licensed.csv"
    source.write_text(
        "indicator_id,period,value,provider\nbtc_spot_etf_flow,2026-08-26,125000000,vendor\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(cli, ["import-indicators", "--file", str(source), "--dry-run"])

    assert result.exit_code == 0
    assert '"observations": 1' in result.output
    assert '"btc_spot_etf_flow"' in result.output
