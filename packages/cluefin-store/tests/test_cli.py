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
