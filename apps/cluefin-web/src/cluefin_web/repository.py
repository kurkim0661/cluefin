from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import RLock
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
        self._query_lock = RLock()

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

    def market_pulse(self) -> dict:
        definitions = self._query_rows(
            """
            SELECT
                indicator_id,
                name_ko,
                name_en,
                domain,
                category,
                provider,
                source_series,
                unit,
                frequency,
                higher_is,
                importance,
                description_ko,
                interpretation_ko,
                source_url,
                availability
            FROM market.indicator_definitions FINAL
            ORDER BY importance DESC, domain ASC, category ASC, indicator_id ASC
            """,
            fallback_columns=[
                "indicator_id",
                "name_ko",
                "name_en",
                "domain",
                "category",
                "provider",
                "source_series",
                "unit",
                "frequency",
                "higher_is",
                "importance",
                "description_ko",
                "interpretation_ko",
                "source_url",
                "availability",
            ],
        )
        observations = self._query_rows(
            """
            SELECT
                indicator_id,
                toString(period) AS observed_on,
                value,
                provider
            FROM market.indicator_observations FINAL
            WHERE period >= today() - 400
            ORDER BY indicator_id ASC, period ASC
            """,
            fallback_columns=["indicator_id", "observed_on", "value", "provider"],
        )
        return _build_market_pulse(definitions, observations)

    def latest_research_report(self) -> dict | None:
        rows = self._query_rows(
            """
            SELECT toString(report_date) AS report_date, toString(report_id) AS report_id,
                   model, status, title, markdown, summary_json, prompt_version,
                   toString(generated_at) AS generated_at
            FROM market.daily_research_reports FINAL
            ORDER BY report_date DESC, generated_at DESC
            LIMIT 1
            """,
            fallback_columns=[
                "report_date",
                "report_id",
                "model",
                "status",
                "title",
                "markdown",
                "summary_json",
                "prompt_version",
                "generated_at",
            ],
        )
        return rows[0] if rows else None

    def research_report_history(self, limit: int = 30) -> list[dict]:
        """Every stored report, newest first. Markdown is omitted to keep the list light."""
        return self._query_rows(
            f"""
            SELECT toString(report_date) AS report_date, toString(report_id) AS report_id,
                   model, status, title, prompt_version,
                   toString(generated_at) AS generated_at,
                   length(markdown) AS markdown_chars
            FROM market.daily_research_reports FINAL
            ORDER BY report_date DESC, generated_at DESC
            LIMIT {max(1, min(int(limit), 200))}
            """,
            fallback_columns=[
                "report_date",
                "report_id",
                "model",
                "status",
                "title",
                "prompt_version",
                "generated_at",
                "markdown_chars",
            ],
        )

    def research_report(self, report_id: str) -> dict | None:
        rows = self._query_rows(
            """
            SELECT toString(report_date) AS report_date, toString(report_id) AS report_id,
                   model, status, title, markdown, summary_json, prompt_version,
                   toString(generated_at) AS generated_at
            FROM market.daily_research_reports FINAL
            WHERE report_id = %(report_id)s
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            fallback_columns=[
                "report_date",
                "report_id",
                "model",
                "status",
                "title",
                "markdown",
                "summary_json",
                "prompt_version",
                "generated_at",
            ],
            parameters={"report_id": report_id},
        )
        return rows[0] if rows else None

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
            FROM market.daily_universe_members AS m FINAL
            INNER JOIN
            (
                SELECT provider, universe_name, max(trade_date) AS trade_date
                FROM market.daily_universe_members
                GROUP BY provider, universe_name
            ) AS latest
                ON m.provider = latest.provider
                AND m.universe_name = latest.universe_name
                AND m.trade_date = latest.trade_date
            WHERE m.trade_date = latest.trade_date
            ORDER BY m.market_country ASC, m.rank ASC, m.symbol ASC
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
            WHERE (t.provider, t.symbol, t.trade_date) IN
            (
                SELECT provider, symbol, max(trade_date)
                FROM market.daily_technical_features
                GROUP BY provider, symbol
            )
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

    def real_estate_meta(self) -> dict:
        """분석 화면이 dimension·fact 선택지를 구성할 수 있도록 카탈로그와 실제 값 목록을 함께 준다."""
        metrics = self._query_rows(
            """
            SELECT metric_id, name_ko, category, deal_type, unit, frequency,
                   description_ko, interpretation_ko, source_url
            FROM market.real_estate_metrics FINAL
            ORDER BY category, metric_id
            """,
            fallback_columns=[
                "metric_id",
                "name_ko",
                "category",
                "deal_type",
                "unit",
                "frequency",
                "description_ko",
                "interpretation_ko",
                "source_url",
            ],
        )
        combos = self._query_rows(
            """
            SELECT metric_id, region, region_tier, property_type, deal_type, count() AS points,
                   toString(min(period)) AS first_period, toString(max(period)) AS last_period
            FROM market.real_estate_observations FINAL
            GROUP BY metric_id, region, region_tier, property_type, deal_type
            ORDER BY metric_id, region, property_type
            """,
            fallback_columns=[
                "metric_id",
                "region",
                "region_tier",
                "property_type",
                "deal_type",
                "points",
                "first_period",
                "last_period",
            ],
        )
        coverage = self._query_rows(
            """
            SELECT count() AS observations, countDistinct(metric_id) AS metrics,
                   countDistinct(region) AS regions,
                   toString(min(period)) AS first_period, toString(max(period)) AS last_period
            FROM market.real_estate_observations FINAL
            """,
            fallback_columns=["observations", "metrics", "regions", "first_period", "last_period"],
        )
        return {
            "metrics": metrics,
            "combinations": combos,
            "dimensions": _real_estate_dimension_options(combos),
            "coverage": coverage[0] if coverage else {"observations": 0, "metrics": 0, "regions": 0},
            "templates": REAL_ESTATE_TEMPLATES,
        }

    def real_estate_query(self, payload: dict) -> dict:
        """사용자가 고른 fact·dimension·필터로 시계열을 만든다."""
        request = _real_estate_request(payload)
        filters: list[str] = []
        parameters: dict[str, Any] = {}
        for index, (column, values) in enumerate(request["filters"].items()):
            if not values:
                continue
            key = f"f{index}"
            filters.append(f"{column} IN %({key})s")
            parameters[key] = values
        if request["start_date"]:
            filters.append("period >= %(start_date)s")
            parameters["start_date"] = request["start_date"]
        if request["end_date"]:
            filters.append("period <= %(end_date)s")
            parameters["end_date"] = request["end_date"]

        bucket = REAL_ESTATE_BUCKETS[request["bucket"]]
        series_column = request["series_dimension"]
        # 비율 모드는 분자·분모를 나누어야 하므로 지표를 결과에 함께 남긴다.
        ratio_mode = request["transform"] == "ratio"
        extra_select = ", metric_id" if ratio_mode and series_column != "metric_id" else ""
        rows = self._query_rows(
            f"""
            SELECT toString({bucket}) AS bucket,
                   {series_column} AS series,
                   {REAL_ESTATE_AGGREGATIONS[request["aggregation"]]}(value) AS value,
                   any(unit) AS unit{extra_select}
            FROM market.real_estate_observations FINAL
            WHERE {" AND ".join(filters)}
            GROUP BY bucket, series{extra_select}
            ORDER BY bucket, series
            """,
            fallback_columns=["bucket", "series", "value", "unit"] + (["metric_id"] if extra_select else []),
            parameters=parameters,
        )
        return _real_estate_result(rows, request)

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
        with self._query_lock:
            return ClickHouseStore(client=self.client).insert_records(table, records)

    def _query_rows(self, sql: str, *, fallback_columns: list[str], parameters: dict | None = None) -> list[dict]:
        try:
            with self._query_lock:
                result = self.client.query(sql, parameters=parameters) if parameters else self.client.query(sql)
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


def _build_market_pulse(definitions: list[dict], observations: list[dict]) -> dict:
    series_by_id: dict[str, list[dict]] = {}
    for row in observations:
        indicator_id = str(row.get("indicator_id") or "")
        period = _parse_date(row.get("observed_on") or row.get("period"))
        if not indicator_id or period is None or row.get("value") is None:
            continue
        series_by_id.setdefault(indicator_id, []).append(
            {"period": period, "value": float(row["value"]), "provider": row.get("provider")}
        )

    indicators: list[dict] = []
    for definition in definitions:
        indicator_id = str(definition.get("indicator_id") or "")
        series = sorted(series_by_id.get(indicator_id, []), key=lambda item: item["period"])
        latest = series[-1] if series else None
        previous = series[-2] if len(series) > 1 else None
        change = latest["value"] - previous["value"] if latest and previous else None
        change_pct = change / abs(previous["value"]) if change is not None and previous["value"] else None
        latest_period = latest["period"] if latest else None
        freshness = _freshness(latest_period, str(definition.get("frequency") or "daily"))
        impact = _impact_assessment(definition, series, freshness)
        level = _level_assessment(str(definition.get("higher_is") or "context"), impact["percentile"])
        indicators.append(
            {
                **definition,
                "importance": int(definition.get("importance") or 0),
                "value": latest["value"] if latest else None,
                "previous_value": previous["value"] if previous else None,
                "change": change,
                "change_pct": change_pct,
                "tone": impact["tone"],
                "impact_score": impact["score"],
                "impact_label": impact["label"],
                "impact_strength": impact["strength"],
                "impact_explanation": impact["explanation"],
                "impact_confidence": impact["confidence"],
                "comparison_label": impact["comparison_label"],
                "comparison_change": impact["comparison_change"],
                "percentile": impact["percentile"],
                "level_label": level["label"],
                "level_tone": level["tone"],
                "period": latest_period.isoformat() if latest_period else None,
                "freshness": freshness,
                "series": [{"period": item["period"].isoformat(), "value": item["value"]} for item in series[-180:]],
            }
        )

    observed = [item for item in indicators if item["value"] is not None]
    scored = [item for item in observed if item["tone"] != "neutral" and item["importance"] >= 2]
    positive = sum(item["importance"] for item in scored if item["tone"] == "positive")
    negative = sum(item["importance"] for item in scored if item["tone"] == "negative")
    regime = _market_regime(positive, negative)
    sections = {
        "rates_liquidity": _section_summary(
            indicators, lambda item: item["category"] in {"rates", "liquidity", "credit", "fx"}
        ),
        "growth_prices": _section_summary(
            indicators, lambda item: item["category"] in {"growth", "inflation", "labor", "commodities"}
        ),
        "korea": _section_summary(indicators, lambda item: item["domain"] == "korea"),
        "equity": _section_summary(indicators, lambda item: item["domain"] == "equity"),
        "crypto": _section_summary(indicators, lambda item: item["domain"] == "crypto"),
    }
    drivers = sorted(
        observed,
        key=lambda item: (
            item["importance"],
            item["freshness"] == "fresh",
            abs(item["impact_score"] or 0),
        ),
        reverse=True,
    )[:16]
    unavailable = [item for item in indicators if item["value"] is None]
    return {
        "regime": regime,
        "coverage": {
            "observed": len(observed),
            "total": len(indicators),
            "fresh": sum(item["freshness"] == "fresh" for item in observed),
            "public_missing": sum(
                item["value"] is None and item.get("availability") in {"public", "derived"} for item in indicators
            ),
            "connection_required": sum(
                item["value"] is None and item.get("availability") in {"api_key", "licensed"} for item in indicators
            ),
        },
        "sections": sections,
        "drivers": drivers,
        "indicators": indicators,
        "unavailable": unavailable,
        "as_of": max((item["period"] for item in observed if item["period"]), default=None),
    }


def _impact_assessment(definition: dict, series: list[dict], freshness: str) -> dict:
    higher_is = str(definition.get("higher_is") or "context")
    frequency = str(definition.get("frequency") or "daily")
    lag, comparison_label = _impact_lag(frequency)
    percentile = _series_percentile(series)
    if not series:
        return {
            "score": None,
            "tone": "neutral",
            "label": "데이터 없음",
            "strength": "none",
            "explanation": "관측값이 없어 시장 영향을 계산하지 못했습니다.",
            "confidence": "none",
            "comparison_label": comparison_label,
            "comparison_change": None,
            "percentile": percentile,
        }
    if higher_is == "context":
        return {
            "score": None,
            "tone": "neutral",
            "label": "판단 보조",
            "strength": "context",
            "explanation": "방향만으로 호재·악재를 정할 수 없어 다른 지표와 함께 해석해야 합니다.",
            "confidence": _impact_confidence(len(series), freshness),
            "comparison_label": comparison_label,
            "comparison_change": None,
            "percentile": percentile,
        }

    effective_lag = min(lag, len(series) - 1)
    if effective_lag <= 0:
        return {
            "score": 0,
            "tone": "neutral",
            "label": "변화 확인 전",
            "strength": "weak",
            "explanation": "비교할 이전 관측값이 더 필요합니다.",
            "confidence": "low",
            "comparison_label": comparison_label,
            "comparison_change": None,
            "percentile": percentile,
        }

    change = series[-1]["value"] - series[-1 - effective_lag]["value"]
    historical_changes = [
        series[index]["value"] - series[index - effective_lag]["value"] for index in range(effective_lag, len(series))
    ]
    scale = math.sqrt(sum(value * value for value in historical_changes) / len(historical_changes))
    direction = 1 if higher_is == "risk_on" else -1
    normalized = direction * change / scale if scale else 0.0
    score = round(100 * math.tanh(normalized / 1.6))
    tone = "positive" if score >= 25 else "negative" if score <= -25 else "neutral"
    label = "우호적" if tone == "positive" else "부담" if tone == "negative" else "중립"
    strength = "strong" if abs(score) >= 65 else "moderate" if abs(score) >= 25 else "weak"
    moved = "상승" if change > 0 else "하락" if change < 0 else "보합"
    if tone == "positive":
        effect = "위험자산에 우호적인 방향입니다"
    elif tone == "negative":
        effect = "위험자산에 부담이 되는 방향입니다"
    else:
        effect = "평소 변동 범위라 방향 신호가 약합니다"
    return {
        "score": score,
        "tone": tone,
        "label": label,
        "strength": strength,
        "explanation": f"{comparison_label} 기준 {moved}했고, {effect}.",
        "confidence": _impact_confidence(len(series), freshness),
        "comparison_label": comparison_label,
        "comparison_change": change,
        "percentile": percentile,
    }


def _impact_lag(frequency: str) -> tuple[int, str]:
    return {
        "hourly": (24, "24시간"),
        "daily": (5, "최근 5거래일"),
        "weekly": (4, "최근 4주"),
        "monthly": (3, "최근 3개월"),
        "quarterly": (1, "직전 분기"),
        "annual": (1, "직전 연도"),
        "event": (1, "직전 발표"),
    }.get(frequency, (1, "직전 관측"))


def _series_percentile(series: list[dict]) -> int | None:
    if not series:
        return None
    latest = series[-1]["value"]
    below_or_equal = sum(item["value"] <= latest for item in series)
    return round(below_or_equal / len(series) * 100)


def _impact_confidence(point_count: int, freshness: str) -> str:
    if freshness == "stale" or point_count < 3:
        return "low"
    if freshness == "fresh" and point_count >= 20:
        return "high"
    return "medium"


def _level_assessment(higher_is: str, percentile: int | None) -> dict[str, str]:
    if percentile is None:
        return {"label": "수준 계산 전", "tone": "neutral"}
    if 30 < percentile < 70:
        return {"label": f"현재 수준 중간권 · {percentile}백분위", "tone": "neutral"}
    zone = "높은" if percentile >= 70 else "낮은"
    if higher_is == "context":
        return {"label": f"현재 {zone} 구간 · {percentile}백분위", "tone": "neutral"}
    supportive = (higher_is == "risk_on" and percentile >= 70) or (higher_is == "risk_off" and percentile <= 30)
    return {
        "label": f"현재 수준 {'우호' if supportive else '부담'} · {percentile}백분위",
        "tone": "positive" if supportive else "negative",
    }


def _freshness(period: date | None, frequency: str) -> str:
    if period is None:
        return "missing"
    limits = {"hourly": 2, "daily": 5, "weekly": 14, "monthly": 50, "quarterly": 130, "annual": 450, "event": 100}
    age = (date.today() - period).days
    if age <= limits.get(frequency, 14):
        return "fresh"
    if age <= limits.get(frequency, 14) * 2:
        return "aging"
    return "stale"


def _market_regime(positive: int, negative: int) -> dict:
    total = positive + negative
    score = (positive - negative) / total if total else 0.0
    if score >= 0.2:
        return {
            "key": "supportive",
            "label": "우호적",
            "headline": "위험자산에 우호적인 신호가 조금 더 많습니다",
            "summary": "방향이 같은 지표가 이어지는지 확인하면서 과도한 낙관은 경계하세요.",
            "score": round(score, 3),
        }
    if score <= -0.2:
        return {
            "key": "defensive",
            "label": "방어적",
            "headline": "금융여건과 경기 신호가 위험자산에 부담을 줍니다",
            "summary": "현금흐름과 재무건전성이 약한 자산의 변동성 확대에 유의하세요.",
            "score": round(score, 3),
        }
    return {
        "key": "mixed",
        "label": "혼조",
        "headline": "지표들이 한 방향으로 모이지 않고 있습니다",
        "summary": "단일 숫자보다 발표 서프라이즈와 3개월 추세를 우선해 읽으세요.",
        "score": round(score, 3),
    }


def _section_summary(indicators: list[dict], predicate) -> dict:
    items = [item for item in indicators if predicate(item)]
    observed = [item for item in items if item["value"] is not None]
    positive = sum(item["tone"] == "positive" for item in observed)
    negative = sum(item["tone"] == "negative" for item in observed)
    if positive > negative:
        tone, label = "positive", "우호"
    elif negative > positive:
        tone, label = "negative", "부담"
    else:
        tone, label = "neutral", "중립"
    return {"tone": tone, "label": label, "observed": len(observed), "total": len(items)}


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


REAL_ESTATE_BUCKETS = {
    "week": "toMonday(period)",
    "month": "toStartOfMonth(period)",
    "quarter": "toStartOfQuarter(period)",
}
REAL_ESTATE_AGGREGATIONS = {"avg": "avg", "sum": "sum", "max": "max", "min": "min", "last": "anyLast"}
REAL_ESTATE_DIMENSIONS = {
    "metric_id": "지표",
    "region": "지역",
    "region_tier": "권역",
    "property_type": "주택유형",
    "deal_type": "거래유형",
}
REAL_ESTATE_TRANSFORMS = {
    "raw": "원값",
    "change": "직전 구간 대비 %",
    "yoy": "전년 동기 대비 %",
    "rebase": "시작 시점 100 기준",
    "ratio": "두 지표 비율 (%)",
}
REAL_ESTATE_TEMPLATES = [
    {
        "id": "capital_sale_index",
        "name": "수도권 아파트 매매가격지수",
        "question": "서울·경기·인천 아파트값이 어느 방향으로 가고 있나?",
        "metric_ids": ["house_sale_price_index"],
        "series_dimension": "region",
        "filters": {"region": ["서울", "경기", "인천", "수도권"], "property_type": ["아파트"]},
        "transform": "raw",
        "chart": "line",
    },
    {
        "id": "capital_sale_yoy",
        "name": "수도권 매매지수 전년 대비 변화율",
        "question": "상승·하락 속도가 지역별로 얼마나 벌어졌나?",
        "metric_ids": ["house_sale_price_index"],
        "series_dimension": "region",
        "filters": {"region": ["서울", "경기", "인천", "지방"], "property_type": ["아파트"]},
        "transform": "yoy",
        "chart": "bar",
    },
    {
        "id": "seoul_property_types",
        "name": "서울 주택유형별 매매지수",
        "question": "아파트·연립·단독 중 무엇이 시장을 끌고 있나?",
        "metric_ids": ["house_sale_price_index"],
        "series_dimension": "property_type",
        "filters": {"region": ["서울"], "property_type": ["아파트", "연립다세대", "단독주택"]},
        "transform": "rebase",
        "chart": "line",
    },
    {
        "id": "seoul_deal_types",
        "name": "서울 매매·전세·월세 지수 비교",
        "question": "매매와 임대차 가격이 같은 방향인가?",
        "metric_ids": ["house_sale_price_index", "house_jeonse_price_index", "house_monthly_rent_index"],
        "series_dimension": "metric_id",
        "filters": {"region": ["서울"], "property_type": ["아파트"]},
        "transform": "rebase",
        "chart": "line",
    },
    {
        "id": "jeonse_ratio_proxy",
        "name": "전세/매매 지수 비율 (전세가율 대용)",
        "question": "전세가 매매를 따라잡고 있나, 벌어지고 있나?",
        "metric_ids": ["house_jeonse_price_index", "house_sale_price_index"],
        "series_dimension": "region",
        "filters": {"region": ["서울", "경기", "인천"], "property_type": ["아파트"]},
        "transform": "ratio",
        "chart": "line",
    },
    {
        "id": "survey_vs_real_deal",
        "name": "조사지수 vs 실거래지수 (서울)",
        "question": "조사 기반 지수와 실제 계약가격이 어긋나고 있나?",
        "metric_ids": ["house_sale_price_index", "apartment_real_transaction_index"],
        "series_dimension": "metric_id",
        "filters": {"region": ["서울"]},
        "transform": "rebase",
        "chart": "line",
    },
    {
        "id": "seoul_subregions",
        "name": "서울 5개 권역 실거래지수",
        "question": "서울 안에서 어느 권역이 먼저 움직이나?",
        "metric_ids": ["apartment_real_transaction_index"],
        "series_dimension": "region",
        "filters": {"region_tier": ["서울권역"]},
        "transform": "rebase",
        "chart": "line",
    },
    {
        "id": "long_run_cycle",
        "name": "10년 장기 사이클 (재지수화)",
        "question": "지금 구간은 지난 10년 사이클 중 어디쯤인가?",
        "metric_ids": ["house_sale_price_index_long"],
        "series_dimension": "region",
        "filters": {"region": ["서울", "경기", "인천", "지방"], "property_type": ["아파트"]},
        "transform": "rebase",
        "chart": "line",
    },
    {
        "id": "long_run_jeonse_gap",
        "name": "10년 매매·전세 장기 비교 (서울)",
        "question": "장기적으로 전세가 매매를 따라왔나?",
        "metric_ids": ["house_sale_price_index_long", "house_jeonse_price_index_long"],
        "series_dimension": "metric_id",
        "filters": {"region": ["서울"], "property_type": ["아파트"]},
        "transform": "rebase",
        "chart": "line",
    },
    {
        "id": "capital_vs_local",
        "name": "수도권 vs 지방 매매지수",
        "question": "수도권 쏠림이 심해지고 있나?",
        "metric_ids": ["house_sale_price_index"],
        "series_dimension": "region",
        "filters": {"region": ["수도권", "지방", "전국"], "property_type": ["종합"]},
        "transform": "rebase",
        "chart": "line",
    },
    {
        "id": "unsold_inventory",
        "name": "미분양 재고 추이",
        "question": "분양시장 수요가 식고 있나?",
        "metric_ids": ["unsold_housing"],
        "series_dimension": "region",
        "filters": {"region": ["서울", "경기", "인천", "수도권"]},
        "transform": "raw",
        "aggregation": "last",
        "chart": "area",
    },
    {
        "id": "housing_permits",
        "name": "주택 인허가 물량 (미래 공급)",
        "question": "2~3년 뒤 입주물량이 늘고 있나 줄고 있나?",
        "metric_ids": ["housing_permits"],
        "series_dimension": "region",
        "filters": {"region": ["서울", "경기", "인천"]},
        "transform": "raw",
        "aggregation": "sum",
        "chart": "bar",
    },
    {
        "id": "land_price",
        "name": "수도권 지가변동률",
        "question": "토지시장 온도는 주택과 같은가?",
        "metric_ids": ["land_price_change"],
        "series_dimension": "region",
        "filters": {"region": ["서울", "경기", "인천", "전국"]},
        "transform": "raw",
        "aggregation": "avg",
        "chart": "bar",
    },
]


def _real_estate_dimension_options(combos: list[dict]) -> dict:
    options: dict[str, list[str]] = {name: [] for name in REAL_ESTATE_DIMENSIONS}
    for row in combos:
        for name in REAL_ESTATE_DIMENSIONS:
            value = row.get(name)
            if value and value not in options[name]:
                options[name].append(str(value))
    for values in options.values():
        values.sort()
    return options


def _real_estate_request(payload: dict) -> dict:
    metric_ids = [str(value) for value in (payload.get("metric_ids") or []) if value]
    if not metric_ids and payload.get("metric_id"):
        metric_ids = [str(payload["metric_id"])]
    if not metric_ids:
        raise ValueError("metric_ids는 최소 1개가 필요합니다.")
    bucket = str(payload.get("bucket") or "month")
    if bucket not in REAL_ESTATE_BUCKETS:
        raise ValueError(f"bucket은 {', '.join(REAL_ESTATE_BUCKETS)} 중 하나여야 합니다.")
    aggregation = str(payload.get("aggregation") or "avg")
    if aggregation not in REAL_ESTATE_AGGREGATIONS:
        raise ValueError(f"aggregation은 {', '.join(REAL_ESTATE_AGGREGATIONS)} 중 하나여야 합니다.")
    series_dimension = str(payload.get("series_dimension") or "region")
    if series_dimension not in REAL_ESTATE_DIMENSIONS:
        raise ValueError(f"series_dimension은 {', '.join(REAL_ESTATE_DIMENSIONS)} 중 하나여야 합니다.")
    transform = str(payload.get("transform") or "raw")
    if transform not in REAL_ESTATE_TRANSFORMS:
        raise ValueError(f"transform은 {', '.join(REAL_ESTATE_TRANSFORMS)} 중 하나여야 합니다.")
    if transform == "ratio" and len(metric_ids) != 2:
        raise ValueError("비율 변환은 지표를 정확히 2개 선택해야 합니다.")

    filters: dict[str, list[str]] = {"metric_id": metric_ids}
    for name in ("region", "region_tier", "property_type", "deal_type"):
        values = [str(value) for value in (payload.get("filters") or {}).get(name, []) if value]
        if values:
            filters[name] = values
    return {
        "metric_id": metric_ids[0],
        "metric_ids": metric_ids,
        "filters": filters,
        "bucket": bucket,
        "aggregation": aggregation,
        "series_dimension": series_dimension,
        "transform": transform,
        "start_date": str(payload["start_date"]) if payload.get("start_date") else None,
        "end_date": str(payload["end_date"]) if payload.get("end_date") else None,
    }


def _shift_period(bucket_date: date, bucket: str, periods: int) -> date:
    if bucket == "week":
        return bucket_date - timedelta(weeks=periods)
    months = periods * (3 if bucket == "quarter" else 1)
    total = bucket_date.year * 12 + (bucket_date.month - 1) - months
    return date(total // 12, total % 12 + 1, 1)


def _periods_per_year(bucket: str) -> int:
    return {"week": 52, "month": 12, "quarter": 4}[bucket]


def _real_estate_result(rows: list[dict], request: dict) -> dict:
    buckets = sorted({str(row["bucket"]) for row in rows})
    by_series: dict[str, dict[str, float]] = {}
    unit = ""
    for row in rows:
        value = row.get("value")
        if value is None:
            continue
        by_series.setdefault(str(row["series"]), {})[str(row["bucket"])] = float(value)
        unit = unit or str(row.get("unit") or "")

    if request["transform"] == "ratio":
        by_series, unit = _real_estate_ratio(rows, request)

    series = []
    for name, values in by_series.items():
        points = _transform_series(values, buckets, request)
        if any(point is not None for point in points):
            series.append({"name": name, "points": points})
    series.sort(key=lambda item: item["name"])
    return {
        "buckets": buckets,
        "series": series,
        "unit": "%" if request["transform"] in {"change", "yoy", "ratio"} else unit,
        "transform": request["transform"],
        "transform_label": REAL_ESTATE_TRANSFORMS[request["transform"]],
        "bucket": request["bucket"],
        "aggregation": request["aggregation"],
        "series_dimension": request["series_dimension"],
        "metric_ids": request["metric_ids"],
    }


def _real_estate_ratio(rows: list[dict], request: dict) -> tuple[dict[str, dict[str, float]], str]:
    """지표 2개를 고른 비율 모드에서는 첫 지표를 분자로 두고 시리즈별로 나눈다."""
    numerator, denominator = request["metric_ids"]
    numerator_values: dict[tuple[str, str], float] = {}
    denominator_values: dict[tuple[str, str], float] = {}
    for row in rows:
        if row.get("value") is None:
            continue
        key = (str(row["series"]), str(row["bucket"]))
        metric = str(row.get("metric_id") or "")
        target = numerator_values if metric == numerator else denominator_values
        target[key] = float(row["value"])
    combined: dict[str, dict[str, float]] = {}
    for (name, bucket), value in numerator_values.items():
        base = denominator_values.get((name, bucket))
        if base:
            combined.setdefault(name, {})[bucket] = value / base * 100
    return combined, "%"


def _transform_series(values: dict[str, float], buckets: list[str], request: dict) -> list[float | None]:
    transform = request["transform"]
    if transform in {"raw", "ratio"}:
        return [_round_point(values.get(bucket)) for bucket in buckets]
    if transform == "rebase":
        base = next((values[bucket] for bucket in buckets if values.get(bucket)), None)
        if not base:
            return [None for _ in buckets]
        return [_round_point(values[bucket] / base * 100) if values.get(bucket) else None for bucket in buckets]
    lag = 1 if transform == "change" else _periods_per_year(request["bucket"])
    points: list[float | None] = []
    for bucket in buckets:
        current = values.get(bucket)
        previous_key = _shift_period(date.fromisoformat(bucket), request["bucket"], lag).isoformat()
        previous = values.get(previous_key)
        points.append(_round_point((current / previous - 1) * 100) if current is not None and previous else None)
    return points


def _round_point(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)
