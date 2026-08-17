from datetime import date
from decimal import Decimal

from cluefin_store.backtest import BacktestCandle, BacktestConfig, BacktestSignal, run_signal_backtest


def test_run_signal_backtest_buys_signal_and_records_equity_and_trade() -> None:
    candles = {
        "005930": [
            BacktestCandle(date(2025, 8, 18), "005930", Decimal("100"), Decimal("100")),
            BacktestCandle(date(2025, 8, 19), "005930", Decimal("110"), Decimal("110")),
            BacktestCandle(date(2025, 8, 20), "005930", Decimal("120"), Decimal("120")),
        ]
    }
    signals = [
        BacktestSignal(
            trade_date=date(2025, 8, 18),
            symbol="005930",
            pattern_name="double_bottom",
            confluence_score=0.9,
        )
    ]

    result = run_signal_backtest(
        candles_by_symbol=candles,
        signals=signals,
        config=BacktestConfig(initial_cash=Decimal("10000"), position_pct=Decimal("0.5"), hold_days=2),
    )

    assert result.total_return > 0
    assert len(result.daily_equity) == 3
    assert result.daily_equity[-1].equity > Decimal("10000")
    assert result.trades[0].side == "buy"
    assert result.trades[-1].side == "sell"
    assert result.trades[-1].realized_pnl > Decimal("0")


def test_run_signal_backtest_requires_selected_indicator_votes() -> None:
    candles = {
        "005930": [
            BacktestCandle(date(2025, 8, 18), "005930", Decimal("100"), Decimal("100")),
            BacktestCandle(date(2025, 8, 19), "005930", Decimal("105"), Decimal("105")),
            BacktestCandle(date(2025, 8, 20), "005930", Decimal("110"), Decimal("110")),
        ]
    }
    signals = [
        BacktestSignal(
            trade_date=date(2025, 8, 18),
            symbol="005930",
            pattern_name="double_bottom",
            confluence_score=0.9,
            direction="bullish",
            indicator_votes={
                "volume_profile": "bullish",
                "vwap": "bullish",
                "ema": "neutral",
                "volume": "bearish",
            },
        )
    ]

    blocked = run_signal_backtest(
        candles_by_symbol=candles,
        signals=signals,
        config=BacktestConfig(
            initial_cash=Decimal("10000"),
            selected_indicators=("volume_profile", "vwap", "ema"),
            min_indicator_agreement=3,
        ),
    )
    allowed = run_signal_backtest(
        candles_by_symbol=candles,
        signals=signals,
        config=BacktestConfig(
            initial_cash=Decimal("10000"),
            selected_indicators=("volume_profile", "vwap", "ema"),
            min_indicator_agreement=2,
        ),
    )

    assert not blocked.trades
    assert allowed.trades[0].side == "buy"
