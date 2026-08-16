from __future__ import annotations

import click

from cluefin_store.db import ClickHouseSettings, ClickHouseStore
from cluefin_store.schema import SCHEMA_STATEMENTS


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
