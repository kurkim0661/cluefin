from __future__ import annotations

import json
from datetime import date

import click
from cluefin_store.db import ClickHouseStore

from cluefin_agent.config import PatModelSettings
from cluefin_agent.graph import build_daily_report_graph, prepare_report_prompt
from cluefin_agent.repository import ResearchDataRepository


@click.group()
def cli() -> None:
    """Cluefin LangGraph research agent."""


@cli.command(name="daily-report")
@click.option("--date", "report_date", type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--dry-run", is_flag=True, help="Collect evidence and print coverage without calling the LLM.")
@click.option("--no-persist", is_flag=True, help="Generate without writing the report to ClickHouse.")
def daily_report_command(report_date, dry_run: bool, no_persist: bool) -> None:
    resolved_date = date(report_date.year, report_date.month, report_date.day) if report_date else date.today()
    store = ClickHouseStore()
    repository = ResearchDataRepository(store)
    if dry_run:
        snapshot = repository.collect_snapshot(resolved_date)
        click.echo(
            json.dumps(
                {
                    "report_date": resolved_date.isoformat(),
                    "coverage": snapshot["coverage"],
                    "patterns": len(snapshot["patterns"]),
                    "signals": len(snapshot["signals"]),
                    "universe": snapshot["universe"],
                    "prompt_chars": len(prepare_report_prompt(snapshot)),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return

    settings = PatModelSettings.from_env()
    store.apply_schema()
    graph = build_daily_report_graph(
        repository=repository,
        model=settings.create_model(),
        model_name=settings.model,
        persist=not no_persist,
    )
    result = graph.invoke({"report_date": resolved_date.isoformat()})
    click.echo(result["markdown"])
    click.echo(
        json.dumps(
            {"status": result["status"], "persisted": result["persisted"], "model": result["model"]},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
