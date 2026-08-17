from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

MONEY_QUANT = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class BacktestCandle:
    trade_date: date
    symbol: str
    close: Decimal
    vwap: Decimal | None = None


@dataclass(frozen=True, slots=True)
class BacktestSignal:
    trade_date: date
    symbol: str
    pattern_name: str
    confluence_score: float
    direction: str = "bullish"
    retest_confirmed: bool = False
    indicator_votes: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_cash: Decimal
    direction_mode: str = "long"
    position_pct: Decimal = Decimal("0.20")
    max_positions: int = 5
    min_confluence: float = 0.7
    hold_days: int = 20
    stop_loss_pct: Decimal = Decimal("0.08")
    take_profit_pct: Decimal = Decimal("0.20")
    fee_bps: Decimal = Decimal("0")
    pattern_names: tuple[str, ...] = ()
    selected_indicators: tuple[str, ...] = ()
    min_indicator_agreement: int = 0
    require_retest: bool = False


@dataclass(frozen=True, slots=True)
class BacktestDailyEquity:
    trade_date: date
    cash: Decimal
    positions_value: Decimal
    equity: Decimal
    drawdown: float


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    trade_date: date
    symbol: str
    side: str
    quantity: int
    price: Decimal
    gross_amount: Decimal
    fee: Decimal
    realized_pnl: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class BacktestResult:
    daily_equity: list[BacktestDailyEquity]
    trades: list[BacktestTrade]
    final_equity: Decimal
    total_return: float
    max_drawdown: float
    win_rate: float


@dataclass(slots=True)
class _Position:
    symbol: str
    quantity: int
    entry_price: Decimal
    entry_date: date
    entry_index: int


def run_signal_backtest(
    *,
    candles_by_symbol: dict[str, list[BacktestCandle]],
    signals: list[BacktestSignal],
    config: BacktestConfig,
) -> BacktestResult:
    ordered_candles = {
        symbol: sorted(candles, key=lambda candle: candle.trade_date)
        for symbol, candles in candles_by_symbol.items()
        if candles
    }
    dates = sorted({candle.trade_date for candles in ordered_candles.values() for candle in candles})
    candle_by_symbol_date = {
        (symbol, candle.trade_date): candle for symbol, candles in ordered_candles.items() for candle in candles
    }
    signal_by_date: dict[date, list[BacktestSignal]] = {}
    for signal in signals:
        if _signal_allowed(signal, config):
            signal_by_date.setdefault(signal.trade_date, []).append(signal)

    cash = _money(config.initial_cash)
    positions: dict[str, _Position] = {}
    trades: list[BacktestTrade] = []
    daily: list[BacktestDailyEquity] = []
    peak = cash
    realized_results: list[Decimal] = []

    for index, trade_date in enumerate(dates):
        for symbol in list(positions):
            candle = candle_by_symbol_date.get((symbol, trade_date))
            if candle is None:
                continue
            position = positions[symbol]
            exit_reason = _exit_reason(position, candle.close, index, config)
            if exit_reason:
                cash, trade = _sell(cash, position, candle, config, exit_reason)
                trades.append(trade)
                realized_results.append(trade.realized_pnl)
                del positions[symbol]

        for signal in sorted(signal_by_date.get(trade_date, []), key=lambda item: item.confluence_score, reverse=True):
            if signal.symbol in positions or len(positions) >= config.max_positions:
                continue
            candle = candle_by_symbol_date.get((signal.symbol, trade_date))
            if candle is None:
                continue
            cash, position, trade = _buy(
                cash, candle, index, config, f"{signal.pattern_name}:{signal.confluence_score:.2f}"
            )
            if position is not None:
                positions[position.symbol] = position
                trades.append(trade)

        positions_value = _money(
            sum(
                (
                    position.quantity
                    * (
                        candle_by_symbol_date.get((symbol, trade_date))
                        or _last_candle_before(ordered_candles[symbol], trade_date)
                    ).close
                    for symbol, position in positions.items()
                ),
                Decimal("0"),
            )
        )
        equity = _money(cash + positions_value)
        peak = max(peak, equity)
        drawdown = float((equity / peak) - Decimal("1")) if peak else 0.0
        daily.append(
            BacktestDailyEquity(
                trade_date=trade_date,
                cash=cash,
                positions_value=positions_value,
                equity=equity,
                drawdown=drawdown,
            )
        )

    final_equity = daily[-1].equity if daily else config.initial_cash
    total_return = float((final_equity / config.initial_cash) - Decimal("1")) if config.initial_cash else 0.0
    max_drawdown = min((row.drawdown for row in daily), default=0.0)
    closed_count = len(realized_results)
    win_rate = sum(1 for result in realized_results if result > 0) / closed_count if closed_count else 0.0
    return BacktestResult(
        daily_equity=daily,
        trades=trades,
        final_equity=final_equity,
        total_return=total_return,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
    )


def _signal_allowed(signal: BacktestSignal, config: BacktestConfig) -> bool:
    if config.direction_mode == "long" and signal.direction != "bullish":
        return False
    if signal.confluence_score < config.min_confluence:
        return False
    if config.pattern_names and signal.pattern_name not in config.pattern_names:
        return False
    if config.require_retest and not signal.retest_confirmed:
        return False
    if config.min_indicator_agreement <= 0:
        return True
    selected = config.selected_indicators or tuple((signal.indicator_votes or {}).keys())
    agreement = sum(1 for indicator in selected if (signal.indicator_votes or {}).get(indicator) == signal.direction)
    return agreement >= config.min_indicator_agreement


def _buy(
    cash: Decimal, candle: BacktestCandle, index: int, config: BacktestConfig, reason: str
) -> tuple[Decimal, _Position | None, BacktestTrade | None]:
    allocation = cash * config.position_pct
    if candle.close <= 0:
        return cash, None, None
    quantity = int(allocation / candle.close)
    if quantity <= 0:
        return cash, None, None
    gross = _money(candle.close * quantity)
    fee = _fee(gross, config)
    total = gross + fee
    if total > cash:
        return cash, None, None
    position = _Position(candle.symbol, quantity, candle.close, candle.trade_date, index)
    trade = BacktestTrade(
        candle.trade_date, candle.symbol, "buy", quantity, candle.close, gross, fee, Decimal("0"), reason
    )
    return _money(cash - total), position, trade


def _sell(
    cash: Decimal, position: _Position, candle: BacktestCandle, config: BacktestConfig, reason: str
) -> tuple[Decimal, BacktestTrade]:
    gross = _money(candle.close * position.quantity)
    fee = _fee(gross, config)
    realized = _money((candle.close - position.entry_price) * position.quantity - fee)
    trade = BacktestTrade(
        candle.trade_date,
        candle.symbol,
        "sell",
        position.quantity,
        candle.close,
        gross,
        fee,
        realized,
        reason,
    )
    return _money(cash + gross - fee), trade


def _exit_reason(position: _Position, close: Decimal, index: int, config: BacktestConfig) -> str | None:
    if close <= position.entry_price * (Decimal("1") - config.stop_loss_pct):
        return "stop_loss"
    if close >= position.entry_price * (Decimal("1") + config.take_profit_pct):
        return "take_profit"
    if index - position.entry_index >= config.hold_days:
        return "hold_days"
    return None


def _last_candle_before(candles: list[BacktestCandle], trade_date: date) -> BacktestCandle:
    previous = candles[0]
    for candle in candles:
        if candle.trade_date > trade_date:
            break
        previous = candle
    return previous


def _fee(gross: Decimal, config: BacktestConfig) -> Decimal:
    return _money(gross * config.fee_bps / Decimal("10000"))


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
