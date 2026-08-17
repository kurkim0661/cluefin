from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from cluefin_store.backtest import BacktestCandle, BacktestConfig, BacktestSignal, run_signal_backtest
from cluefin_store.db import ClickHouseStore
from cluefin_store.models import (
    DailyOhlcv,
    PaperAccount,
    PaperBacktestDailyEquity,
    PaperBacktestRun,
    PaperBacktestTrade,
    PaperStrategy,
)
from cluefin_store.patterns import detect_pattern_candidates


class DashboardRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "DashboardRepository":
        return cls(ClickHouseStore().client())

    def snapshot(self) -> dict:
        return {
            "overview": self.market_overview(),
            "performance": self.pattern_performance(),
            "signals": self.latest_signals(),
            "universe": self.universe_members(),
            "technicals": self.latest_technicals(),
            "sentiment": self.latest_sentiment(),
        }

    def market_overview(self) -> dict:
        rows = self._query_rows(
            """
            SELECT
                (SELECT countDistinct(symbol) FROM market.daily_universe_members) AS symbols,
                (SELECT count() FROM market.daily_pattern_events) AS events,
                (SELECT count() FROM market.daily_pattern_outcomes) AS outcomes,
                toString((SELECT max(trade_date) FROM market.daily_ohlcv)) AS as_of
            """,
            fallback_columns=["symbols", "events", "outcomes", "as_of"],
        )
        if not rows:
            return {"symbols": 0, "events": 0, "outcomes": 0, "as_of": None}
        return rows[0]

    def pattern_performance(self, limit: int = 50) -> list[dict]:
        return self._query_rows(
            f"""
            SELECT
                pattern_name,
                horizon_days,
                htf_trend,
                volume_profile_confluence,
                retest_confirmed,
                sample_count,
                round(target_hit_rate, 4) AS target_hit_rate,
                round(avg_return_at_horizon, 4) AS avg_return_at_horizon,
                round(avg_confluence_score, 4) AS avg_confluence_score
            FROM market.pattern_performance_summary
            ORDER BY target_hit_rate DESC, sample_count DESC
            LIMIT {int(limit)}
            """,
            fallback_columns=[
                "pattern_name",
                "horizon_days",
                "htf_trend",
                "volume_profile_confluence",
                "retest_confirmed",
                "sample_count",
                "target_hit_rate",
                "avg_return_at_horizon",
                "avg_confluence_score",
            ],
        )

    def latest_signals(self, limit: int = 100) -> list[dict]:
        return self._query_rows(
            f"""
            SELECT
                e.symbol AS symbol,
                ifNull(anyLast(u.name), e.symbol) AS name,
                e.pattern_name AS pattern_name,
                toString(e.detection_date) AS detection_date,
                round(e.confluence_score, 4) AS confluence_score,
                e.htf_trend AS htf_trend,
                e.volume_profile_confluence AS volume_profile_confluence,
                e.retest_confirmed AS retest_confirmed
            FROM market.daily_pattern_events AS e
            LEFT JOIN market.daily_universe_members AS u
                ON e.provider = u.provider
                AND e.symbol = u.symbol
            WHERE e.detection_date >= today() - 30
            GROUP BY
                e.symbol,
                e.pattern_name,
                e.detection_date,
                e.confluence_score,
                e.htf_trend,
                e.volume_profile_confluence,
                e.retest_confirmed
            ORDER BY e.confluence_score DESC, e.detection_date DESC
            LIMIT {int(limit)}
            """,
            fallback_columns=[
                "symbol",
                "name",
                "pattern_name",
                "detection_date",
                "confluence_score",
                "htf_trend",
                "volume_profile_confluence",
                "retest_confirmed",
            ],
        )

    def universe_members(self, limit: int = 200) -> list[dict]:
        return self._query_rows(
            f"""
            SELECT
                toString(m.trade_date) AS trade_date,
                m.provider,
                m.universe_name,
                m.market_country,
                m.rank,
                m.symbol,
                m.name,
                m.selection_metric_name,
                m.selection_metric_value
            FROM market.daily_universe_members AS m
            WHERE m.trade_date = (SELECT max(trade_date) FROM market.daily_universe_members)
            ORDER BY m.rank ASC, m.symbol ASC
            LIMIT {int(limit)}
            """,
            fallback_columns=[
                "trade_date",
                "provider",
                "universe_name",
                "market_country",
                "rank",
                "symbol",
                "name",
                "selection_metric_name",
                "selection_metric_value",
            ],
        )

    def latest_technicals(self, limit: int = 200) -> list[dict]:
        return self._query_rows(
            f"""
            SELECT
                toString(t.trade_date) AS trade_date,
                t.symbol,
                t.vwap,
                t.ema_20,
                t.ema_50,
                t.ema_200,
                t.rsi_14,
                t.rsi_divergence
            FROM market.daily_technical_features AS t FINAL
            WHERE t.trade_date = (SELECT max(trade_date) FROM market.daily_technical_features)
            ORDER BY t.symbol ASC
            LIMIT {int(limit)}
            """,
            fallback_columns=[
                "trade_date",
                "symbol",
                "vwap",
                "ema_20",
                "ema_50",
                "ema_200",
                "rsi_14",
                "rsi_divergence",
            ],
        )

    def latest_sentiment(self, limit: int = 200) -> list[dict]:
        return self._query_rows(
            f"""
            SELECT
                symbol,
                item_count,
                avg_sentiment_score,
                sentiment_label,
                latest_reason,
                toString(latest_at) AS latest_at
            FROM market.symbol_sentiment_summary
            ORDER BY latest_at DESC, symbol ASC
            LIMIT {int(limit)}
            """,
            fallback_columns=[
                "symbol",
                "item_count",
                "avg_sentiment_score",
                "sentiment_label",
                "latest_reason",
                "latest_at",
            ],
        )

    def symbol_chart(self, symbol: str, days: int = 180) -> dict:
        safe_symbol = _quote_string(symbol)
        candles = self._query_rows(
            f"""
            SELECT
                toString(o.trade_date) AS trade_date,
                o.symbol,
                o.open,
                o.high,
                o.low,
                o.close,
                o.volume,
                o.trading_amount,
                t.vwap,
                t.ema_20,
                t.ema_50,
                t.ema_200,
                t.rsi_14,
                t.rsi_divergence
            FROM
            (
                SELECT trade_date, provider, symbol, open, high, low, close, volume, trading_amount
                FROM market.daily_ohlcv FINAL
                WHERE symbol = '{safe_symbol}'
                ORDER BY trade_date DESC
                LIMIT {int(days)}
            ) AS o
            LEFT JOIN
            (
                SELECT trade_date, provider, symbol, vwap, ema_20, ema_50, ema_200, rsi_14, rsi_divergence
                FROM market.daily_technical_features FINAL
                WHERE symbol = '{safe_symbol}'
            ) AS t
                ON o.provider = t.provider
                AND o.symbol = t.symbol
                AND o.trade_date = t.trade_date
            ORDER BY o.trade_date ASC
            """,
            fallback_columns=[
                "trade_date",
                "symbol",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "trading_amount",
                "vwap",
                "ema_20",
                "ema_50",
                "ema_200",
                "rsi_14",
                "rsi_divergence",
            ],
        )
        volume_profile = self._query_rows(
            f"""
            SELECT
                toString(trade_date) AS trade_date,
                lookback_days,
                poc_price,
                value_area_low,
                value_area_high,
                bins_json
            FROM market.daily_volume_profile_levels FINAL
            WHERE symbol = '{safe_symbol}'
            ORDER BY trade_date DESC
            LIMIT 1
            """,
            fallback_columns=[
                "trade_date",
                "lookback_days",
                "poc_price",
                "value_area_low",
                "value_area_high",
                "bins_json",
            ],
        )
        for profile in volume_profile:
            bins = _parse_json_list(profile.get("bins_json"))
            profile["bins"] = bins
        patterns = self._query_rows(
            f"""
            SELECT
                pattern_name,
                toString(detection_date) AS detection_date,
                neckline_price,
                support_price,
                resistance_price,
                target_price,
                confluence_score,
                feature_json
            FROM market.daily_pattern_events FINAL
            WHERE symbol = '{safe_symbol}'
            ORDER BY detection_date DESC, confluence_score DESC
            LIMIT 50
            """,
            fallback_columns=[
                "pattern_name",
                "detection_date",
                "neckline_price",
                "support_price",
                "resistance_price",
                "target_price",
                "confluence_score",
                "feature_json",
            ],
        )
        for pattern in patterns:
            pattern["features"] = _parse_json_object(pattern.get("feature_json"))
        patterns = _dedupe_chart_patterns(patterns)
        sentiment_items = self._query_rows(
            f"""
            SELECT
                title,
                source,
                url,
                toString(coalesce(published_at, collected_at)) AS published_at,
                summary,
                sentiment_label,
                sentiment_score,
                sentiment_reason
            FROM market.symbol_sentiment_items FINAL
            WHERE symbol = '{safe_symbol}'
            ORDER BY coalesce(published_at, collected_at) DESC
            LIMIT 10
            """,
            fallback_columns=[
                "title",
                "source",
                "url",
                "published_at",
                "summary",
                "sentiment_label",
                "sentiment_score",
                "sentiment_reason",
            ],
        )
        return {
            "symbol": symbol,
            "candles": candles,
            "volume_profile": volume_profile,
            "patterns": patterns,
            "pattern_candidates": _detect_chart_candidates(candles),
            "sentiment_items": sentiment_items,
        }

    def paper_dashboard(self) -> dict:
        return {
            "accounts": self._paper_accounts(),
            "strategies": self._paper_strategies(),
            "backtests": self._paper_backtests(),
        }

    def create_paper_account(self, payload: dict) -> dict:
        now = datetime.now()
        account = PaperAccount(
            account_id=str(payload.get("account_id") or f"paper-{uuid4()}"),
            name=str(payload.get("name") or "Paper Account"),
            base_currency=str(payload.get("base_currency") or "KRW"),
            initial_cash=_decimal(payload.get("initial_cash"), Decimal("10000000")),
            is_active=1,
            created_at=now,
            updated_at=now,
        )
        self._insert_records("portfolio.paper_accounts", [account])
        return account.as_insert_row()

    def create_paper_strategy(self, payload: dict) -> dict:
        now = datetime.now()
        config = _strategy_config_from_payload(payload)
        strategy = PaperStrategy(
            strategy_id=str(payload.get("strategy_id") or f"strategy-{uuid4()}"),
            name=str(payload.get("name") or "Confluence Strategy"),
            strategy_type=str(payload.get("strategy_type") or "signal_backtest"),
            config_json=json.dumps(config, ensure_ascii=False, sort_keys=True),
            is_active=1,
            created_at=now,
            updated_at=now,
        )
        self._insert_records("portfolio.paper_strategies", [strategy])
        return strategy.as_insert_row()

    def run_paper_backtest(self, payload: dict) -> dict:
        accounts = self._paper_accounts(account_id=str(payload.get("account_id") or ""))
        strategies = self._paper_strategies(strategy_id=str(payload.get("strategy_id") or ""))
        if not accounts:
            raise ValueError("paper account not found")
        if not strategies:
            raise ValueError("paper strategy not found")

        account = accounts[0]
        strategy = strategies[0]
        years = max(1, int(payload.get("years", 1)))
        end_date = self._latest_market_date() or date.today()
        start_date = end_date - timedelta(days=365 * years)
        config_json = _parse_json_object(strategy.get("config_json"))
        config = BacktestConfig(
            initial_cash=_decimal(account.get("initial_cash"), Decimal("10000000")),
            position_pct=_decimal(payload.get("position_pct") or config_json.get("position_pct"), Decimal("0.20")),
            max_positions=int(payload.get("max_positions") or config_json.get("max_positions") or 5),
            min_confluence=float(payload.get("min_confluence") or config_json.get("min_confluence") or 0.7),
            hold_days=int(payload.get("hold_days") or config_json.get("hold_days") or 20),
            stop_loss_pct=_decimal(payload.get("stop_loss_pct") or config_json.get("stop_loss_pct"), Decimal("0.08")),
            take_profit_pct=_decimal(
                payload.get("take_profit_pct") or config_json.get("take_profit_pct"),
                Decimal("0.20"),
            ),
            fee_bps=_decimal(payload.get("fee_bps") or config_json.get("fee_bps"), Decimal("0")),
            pattern_names=tuple(config_json.get("pattern_names") or ()),
            selected_indicators=tuple(config_json.get("selected_indicators") or ()),
            min_indicator_agreement=int(config_json.get("min_indicator_agreement") or 0),
            require_retest=bool(config_json.get("require_retest")),
        )
        candles_by_symbol = self._backtest_candles(start_date, end_date)
        signals = self._backtest_signals(start_date, end_date)
        result = run_signal_backtest(candles_by_symbol=candles_by_symbol, signals=signals, config=config)

        now = datetime.now()
        run_id = uuid4()
        run = PaperBacktestRun(
            run_id=run_id,
            account_id=str(account["account_id"]),
            strategy_id=str(strategy["strategy_id"]),
            start_date=start_date,
            end_date=end_date,
            initial_cash=config.initial_cash,
            final_equity=result.final_equity,
            total_return=result.total_return,
            max_drawdown=result.max_drawdown,
            trade_count=len(result.trades),
            win_rate=result.win_rate,
            params_json=json.dumps(
                {"years": years, "strategy_config": config_json}, ensure_ascii=False, sort_keys=True
            ),
            created_at=now,
        )
        equity_rows = [
            PaperBacktestDailyEquity(
                run_id=run_id,
                trade_date=row.trade_date,
                cash=row.cash,
                positions_value=row.positions_value,
                equity=row.equity,
                drawdown=row.drawdown,
                created_at=now,
            )
            for row in result.daily_equity
        ]
        trade_rows = [
            PaperBacktestTrade(
                run_id=run_id,
                trade_date=trade.trade_date,
                symbol=trade.symbol,
                side=trade.side,
                quantity=trade.quantity,
                price=trade.price,
                gross_amount=trade.gross_amount,
                fee=trade.fee,
                realized_pnl=trade.realized_pnl,
                reason=trade.reason,
                created_at=now,
            )
            for trade in result.trades
        ]
        self._insert_records("portfolio.paper_backtest_runs", [run])
        self._insert_records("portfolio.paper_backtest_daily_equity", equity_rows)
        self._insert_records("portfolio.paper_backtest_trades", trade_rows)
        return {
            "run_id": str(run_id),
            "account_id": run.account_id,
            "strategy_id": run.strategy_id,
            "start_date": run.start_date.isoformat(),
            "end_date": run.end_date.isoformat(),
            "initial_cash": float(run.initial_cash),
            "final_equity": float(run.final_equity),
            "total_return": run.total_return,
            "max_drawdown": run.max_drawdown,
            "trade_count": run.trade_count,
            "win_rate": run.win_rate,
            "daily_equity": [asdict(row) for row in result.daily_equity[-10:]],
            "trades": [asdict(trade) for trade in result.trades[-20:]],
        }

    def _paper_accounts(self, account_id: str = "", limit: int = 50) -> list[dict]:
        where = f"WHERE account_id = '{_quote_string(account_id)}'" if account_id else "WHERE is_active = 1"
        return self._query_rows(
            f"""
            SELECT
                account_id,
                name,
                base_currency,
                initial_cash,
                is_active,
                toString(created_at) AS created_at,
                toString(updated_at) AS updated_at
            FROM portfolio.paper_accounts FINAL
            {where}
            ORDER BY updated_at DESC
            LIMIT {int(limit)}
            """,
            fallback_columns=[
                "account_id",
                "name",
                "base_currency",
                "initial_cash",
                "is_active",
                "created_at",
                "updated_at",
            ],
        )

    def _paper_strategies(self, strategy_id: str = "", limit: int = 50) -> list[dict]:
        where = f"WHERE strategy_id = '{_quote_string(strategy_id)}'" if strategy_id else "WHERE is_active = 1"
        return self._query_rows(
            f"""
            SELECT
                strategy_id,
                name,
                strategy_type,
                config_json,
                is_active,
                toString(created_at) AS created_at,
                toString(updated_at) AS updated_at
            FROM portfolio.paper_strategies FINAL
            {where}
            ORDER BY updated_at DESC
            LIMIT {int(limit)}
            """,
            fallback_columns=[
                "strategy_id",
                "name",
                "strategy_type",
                "config_json",
                "is_active",
                "created_at",
                "updated_at",
            ],
        )

    def _paper_backtests(self, limit: int = 20) -> list[dict]:
        return self._query_rows(
            f"""
            SELECT
                toString(run_id) AS run_id,
                account_id,
                strategy_id,
                toString(start_date) AS start_date,
                toString(end_date) AS end_date,
                initial_cash,
                final_equity,
                total_return,
                max_drawdown,
                trade_count,
                win_rate,
                toString(created_at) AS created_at
            FROM portfolio.paper_backtest_runs FINAL
            ORDER BY created_at DESC
            LIMIT {int(limit)}
            """,
            fallback_columns=[
                "run_id",
                "account_id",
                "strategy_id",
                "start_date",
                "end_date",
                "initial_cash",
                "final_equity",
                "total_return",
                "max_drawdown",
                "trade_count",
                "win_rate",
                "created_at",
            ],
        )

    def _latest_market_date(self) -> date | None:
        rows = self._query_rows(
            "SELECT toString(max(trade_date)) AS trade_date FROM market.daily_ohlcv",
            fallback_columns=["trade_date"],
        )
        value = rows[0].get("trade_date") if rows else None
        return _parse_date(value)

    def _backtest_candles(self, start_date: date, end_date: date) -> dict[str, list[BacktestCandle]]:
        rows = self._query_rows(
            f"""
            SELECT
                toString(o.trade_date) AS trade_date,
                o.symbol,
                o.close,
                t.vwap
            FROM
            (
                SELECT trade_date, provider, symbol, close
                FROM market.daily_ohlcv FINAL
                WHERE trade_date BETWEEN toDate('{start_date.isoformat()}') AND toDate('{end_date.isoformat()}')
            ) AS o
            LEFT JOIN
            (
                SELECT trade_date, provider, symbol, vwap
                FROM market.daily_technical_features FINAL
                WHERE trade_date BETWEEN toDate('{start_date.isoformat()}') AND toDate('{end_date.isoformat()}')
            ) AS t
                ON o.provider = t.provider
                AND o.symbol = t.symbol
                AND o.trade_date = t.trade_date
            ORDER BY o.symbol ASC, o.trade_date ASC
            """,
            fallback_columns=["trade_date", "symbol", "close", "vwap"],
        )
        candles: dict[str, list[BacktestCandle]] = {}
        for row in rows:
            trade_date = _parse_date(row.get("trade_date"))
            if trade_date is None:
                continue
            symbol = str(row.get("symbol") or "")
            candles.setdefault(symbol, []).append(
                BacktestCandle(
                    trade_date=trade_date,
                    symbol=symbol,
                    close=_decimal(row.get("close"), Decimal("0")),
                    vwap=_decimal(row.get("vwap"), Decimal("0")) if row.get("vwap") is not None else None,
                )
            )
        return candles

    def _backtest_signals(self, start_date: date, end_date: date) -> list[BacktestSignal]:
        rows = self._query_rows(
            f"""
            SELECT
                toString(detection_date) AS trade_date,
                symbol,
                pattern_name,
                direction,
                confluence_score,
                retest_confirmed,
                htf_trend,
                volume_profile_confluence,
                feature_json,
                close,
                vwap,
                ema_20,
                ema_50,
                ema_200,
                rsi_divergence
            FROM
            (
                SELECT
                    detection_date,
                    provider,
                    symbol,
                    pattern_name,
                    direction,
                    confluence_score,
                    retest_confirmed,
                    htf_trend,
                    volume_profile_confluence,
                    feature_json
                FROM market.daily_pattern_events FINAL
                WHERE detection_date BETWEEN toDate('{start_date.isoformat()}') AND toDate('{end_date.isoformat()}')
            ) AS e
            LEFT JOIN
            (
                SELECT trade_date, provider, symbol, close, vwap, ema_20, ema_50, ema_200, rsi_divergence
                FROM market.daily_technical_features FINAL
                WHERE trade_date BETWEEN toDate('{start_date.isoformat()}') AND toDate('{end_date.isoformat()}')
            ) AS t
                ON e.provider = t.provider
                AND e.symbol = t.symbol
                AND e.detection_date = t.trade_date
            ORDER BY detection_date ASC, confluence_score DESC
            """,
            fallback_columns=[
                "trade_date",
                "symbol",
                "pattern_name",
                "direction",
                "confluence_score",
                "retest_confirmed",
                "htf_trend",
                "volume_profile_confluence",
                "feature_json",
                "close",
                "vwap",
                "ema_20",
                "ema_50",
                "ema_200",
                "rsi_divergence",
            ],
        )
        signals: list[BacktestSignal] = []
        for row in rows:
            trade_date = _parse_date(row.get("trade_date"))
            if trade_date is None:
                continue
            signals.append(
                BacktestSignal(
                    trade_date=trade_date,
                    symbol=str(row.get("symbol") or ""),
                    pattern_name=str(row.get("pattern_name") or ""),
                    confluence_score=float(row.get("confluence_score") or 0),
                    direction=str(row.get("direction") or "bullish"),
                    retest_confirmed=bool(row.get("retest_confirmed")),
                    indicator_votes=_indicator_votes_from_signal_row(row),
                )
            )
        return signals

    def _insert_records(self, table: str, records: list[Any]) -> int:
        return ClickHouseStore(client=self.client).insert_records(table, records)

    def _query_rows(self, sql: str, *, fallback_columns: list[str]) -> list[dict]:
        try:
            result = self.client.query(sql)
        except Exception:
            return []
        columns = getattr(result, "column_names", None) or fallback_columns
        return [dict(zip(columns, row, strict=False)) for row in result.result_rows]


def _quote_string(value: str) -> str:
    return value.replace("'", "\\'")


def _parse_json_object(value: Any) -> dict:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_json_list(value: Any) -> list:
    if not isinstance(value, str) or not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _strategy_config_from_payload(payload: dict) -> dict:
    selected_indicators = _payload_list(
        payload,
        "selected_indicators",
        default=["volume_profile", "vwap", "ema"],
    )
    min_indicator_agreement = int(payload.get("min_indicator_agreement") or min(3, len(selected_indicators)) or 0)
    return {
        "min_confluence": float(payload.get("min_confluence", 0.7)),
        "position_pct": str(payload.get("position_pct", "0.20")),
        "max_positions": int(payload.get("max_positions", 5)),
        "hold_days": int(payload.get("hold_days", 20)),
        "stop_loss_pct": str(payload.get("stop_loss_pct", "0.08")),
        "take_profit_pct": str(payload.get("take_profit_pct", "0.20")),
        "fee_bps": str(payload.get("fee_bps", "0")),
        "pattern_names": _payload_list(payload, "pattern_names"),
        "selected_indicators": selected_indicators,
        "min_indicator_agreement": min_indicator_agreement,
        "require_retest": _payload_bool(payload.get("require_retest")),
        "allow_forming": _payload_bool(payload.get("allow_forming")),
    }


def _payload_list(payload: dict, key: str, default: list[str] | None = None) -> list[str]:
    value = payload.get(key)
    if value is None:
        return list(default or [])
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(value)] if str(value) else list(default or [])


def _payload_bool(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


def _indicator_votes_from_signal_row(row: dict) -> dict[str, str]:
    direction = str(row.get("direction") or "bullish")
    feature = _parse_json_object(row.get("feature_json"))
    votes = {
        "volume_profile": direction if row.get("volume_profile_confluence") else "neutral",
        "retest": direction if row.get("retest_confirmed") else "neutral",
        "htf_trend": _normalized_vote(row.get("htf_trend")),
        "vwap": _price_vote(row.get("close"), row.get("vwap")),
        "ema": _ema_vote(row),
        "rsi_divergence": _normalized_vote(row.get("rsi_divergence")),
        "volume": direction
        if _decimal(feature.get("breakout_volume_ratio"), Decimal("0")) >= Decimal("1.5")
        else "neutral",
    }
    return votes


def _normalized_vote(value: Any) -> str:
    vote = str(value or "neutral")
    return vote if vote in {"bullish", "bearish"} else "neutral"


def _price_vote(close: Any, baseline: Any) -> str:
    if close is None or baseline is None:
        return "neutral"
    close_value = _decimal(close, Decimal("0"))
    baseline_value = _decimal(baseline, Decimal("0"))
    if close_value > baseline_value:
        return "bullish"
    if close_value < baseline_value:
        return "bearish"
    return "neutral"


def _ema_vote(row: dict) -> str:
    ema_20 = row.get("ema_20")
    ema_50 = row.get("ema_50")
    if ema_20 is None or ema_50 is None:
        return "neutral"
    if _decimal(ema_20, Decimal("0")) > _decimal(ema_50, Decimal("0")):
        return "bullish"
    if _decimal(ema_20, Decimal("0")) < _decimal(ema_50, Decimal("0")):
        return "bearish"
    return "neutral"


def _decimal(value: Any, default: Decimal) -> Decimal:
    if value is None or value == "":
        return default
    return Decimal(str(value))


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _detect_chart_candidates(candles: list[dict]) -> list[dict]:
    daily_candles: list[DailyOhlcv] = []
    run_id = UUID("00000000-0000-0000-0000-000000000000")
    collected_at = datetime.now()
    for row in candles:
        trade_date = _parse_date(row.get("trade_date"))
        if trade_date is None:
            continue
        daily_candles.append(
            DailyOhlcv(
                trade_date=trade_date,
                provider="chart",
                symbol=str(row.get("symbol") or ""),
                open=_decimal(row.get("open"), Decimal("0")),
                high=_decimal(row.get("high"), Decimal("0")),
                low=_decimal(row.get("low"), Decimal("0")),
                close=_decimal(row.get("close"), Decimal("0")),
                volume=int(row.get("volume") or 0),
                trading_amount=_decimal(row.get("trading_amount"), Decimal("0")),
                run_id=run_id,
                collected_at=collected_at,
            )
        )
    return [candidate.as_chart_dict() for candidate in detect_pattern_candidates(daily_candles)]


def _dedupe_chart_patterns(patterns: list[dict], limit: int = 12) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple] = set()
    for pattern in patterns:
        key = (
            pattern.get("pattern_name"),
            pattern.get("neckline_price"),
            pattern.get("support_price"),
            pattern.get("resistance_price"),
            pattern.get("target_price"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(pattern)
        if len(deduped) >= limit:
            break
    return deduped
