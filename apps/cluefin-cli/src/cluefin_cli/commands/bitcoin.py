from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import click
import pandas as pd
from loguru import logger
from rich.console import Console
from rich.table import Table

from cluefin_cli.data.upbit import UpbitDataFetcher

console = Console()


@click.command(name="btc")
@click.option("--market", default="KRW-BTC", show_default=True, help="Upbit market id (e.g., KRW-BTC)")
@click.option("--days", default=365, show_default=True, type=int, help="History length in days")
@click.option(
    "--out",
    "out_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Output CSV path [default: data/btc/<market>_1h_<start>_<end>.csv]",
)
def bitcoin(market: str, days: int, out_path: Optional[Path]):
    """Fetch Bitcoin hourly candles from Upbit and save them to CSV."""
    console.print(f"[bold blue]Fetching {days}d of 1h candles for {market} from Upbit...[/bold blue]")

    try:
        fetcher = UpbitDataFetcher()
        df = fetcher.get_hourly_candles(market=market, start=datetime.now(timezone.utc) - timedelta(days=days))
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.error(f"BTC fetch error for {market}: {e}")
        return

    if df.empty:
        console.print("[yellow]No candle data returned.[/yellow]")
        return

    if out_path is None:
        out_path = Path("data/btc") / f"{market}_1h_{df.index[0]:%Y%m%d}_{df.index[-1]:%Y%m%d}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path)

    _print_summary(market, df, out_path)


def _print_summary(market: str, df: pd.DataFrame, out_path: Path):
    table = Table(title=f"Upbit {market} 1h candles")
    table.add_column("항목", style="cyan")
    table.add_column("값", justify="right")

    table.add_row("rows", f"{len(df):,}")
    table.add_row("기간", f"{df.index[0]:%Y-%m-%d %H:%M} ~ {df.index[-1]:%Y-%m-%d %H:%M} UTC")
    table.add_row("최저가", f"{df['low'].min():,.0f}")
    table.add_row("최고가", f"{df['high'].max():,.0f}")
    table.add_row("마지막 종가", f"{df['close'].iloc[-1]:,.0f}")
    table.add_row("저장 위치", str(out_path))

    console.print(table)


if __name__ == "__main__":
    bitcoin()  # pragma: no cover
