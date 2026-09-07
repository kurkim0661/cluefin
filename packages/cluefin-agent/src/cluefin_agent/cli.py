from __future__ import annotations

import json
from datetime import date

import click
from cluefin_store.db import ClickHouseStore
from cluefin_store.env import load_env_file
from cluefin_store.sql_console import run_read_only

from cluefin_agent.config import PatModelSettings
from cluefin_agent.gateway import gateway_hint
from cluefin_agent.graph import build_daily_report_graph, prepare_report_prompt
from cluefin_agent.repository import ResearchDataRepository
from cluefin_agent.sql_agent import translate_question


@click.group()
def cli() -> None:
    """Cluefin LangGraph research agent."""
    # CLUEFIN_LLM_* 를 .env에 적어 두면 export 없이도 읽히게 한다.
    load_env_file()


@cli.command(name="nl2sql")
@click.argument("question", nargs=-1, required=True)
@click.option("--run", "run_query", is_flag=True, help="검증된 SQL을 바로 실행해 결과 표까지 출력합니다.")
@click.option("--rows", default=20, show_default=True, help="--run으로 출력할 최대 행 수.")
@click.option("--json", "as_json", is_flag=True, help="SQL·설명·주의를 JSON 한 덩어리로 출력합니다.")
def nl2sql_command(question: tuple[str, ...], run_query: bool, rows: int, as_json: bool) -> None:
    """자연어 질문을 ClickHouse SQL로 바꿉니다. 예: cluefin-agent nl2sql "수도권 미분양 최근 1년 추이"."""
    settings = PatModelSettings.from_env()
    client = ClickHouseStore().client()
    try:
        result = translate_question(
            client=client,
            model=settings.create_model(),
            model_name=settings.model,
            question=" ".join(question),
        )
    except Exception as exc:  # 게이트웨이 한도·인증·네트워크 실패를 트레이스백 대신 한 줄로 보여 준다.
        raise click.ClickException(gateway_hint(f"{type(exc).__name__}: {exc}")) from exc
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        click.echo(result["sql"] or "(SQL을 만들지 못했습니다)")
        if result["explanation"]:
            click.echo(f"\n# {result['explanation']}")
        for note in result["notes"]:
            click.echo(f"# 주의: {note}")
        if result["status"] != "validated":
            click.echo(f"\n# 검증 실패({result['attempts']}회 시도): {result['error']}", err=True)
    if not run_query:
        return
    if result["status"] != "validated":
        raise SystemExit(1)
    output = run_read_only(client, result["sql"], max_rows=rows)
    click.echo("\n" + " | ".join(output["columns"]))
    for row in output["rows"]:
        click.echo(" | ".join("" if value is None else str(value) for value in row))
    suffix = " (잘림)" if output["truncated"] else ""
    click.echo(f"\n{output['row_count']}행 · {output['elapsed_ms']}ms{suffix}")


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
                    "real_estate": len(snapshot["real_estate"]),
                    "previous_reports": [item["report_date"] for item in snapshot["previous_reports"]],
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
    try:
        result = graph.invoke({"report_date": resolved_date.isoformat()})
    except Exception as exc:  # nl2sql과 같게 게이트웨이 실패를 한 줄로 알린다.
        raise click.ClickException(gateway_hint(f"{type(exc).__name__}: {exc}")) from exc
    click.echo(result["markdown"])
    click.echo(
        json.dumps(
            {"status": result["status"], "persisted": result["persisted"], "model": result["model"]},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
