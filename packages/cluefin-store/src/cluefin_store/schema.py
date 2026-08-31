from __future__ import annotations

import re

SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE DATABASE IF NOT EXISTS store",
    "CREATE DATABASE IF NOT EXISTS market",
    "CREATE DATABASE IF NOT EXISTS dart",
    "CREATE DATABASE IF NOT EXISTS portfolio",
    """
    CREATE TABLE IF NOT EXISTS store.universe_definitions
    (
        universe_name LowCardinality(String),
        universe_type LowCardinality(String),
        provider Nullable(String),
        market_country LowCardinality(String),
        params_json String,
        is_active UInt8,
        updated_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY universe_name
    """,
    """
    CREATE TABLE IF NOT EXISTS store.ingest_runs
    (
        run_id UUID,
        provider LowCardinality(String),
        job_name LowCardinality(String),
        trade_date Nullable(Date),
        started_at DateTime64(3, 'Asia/Seoul'),
        finished_at Nullable(DateTime64(3, 'Asia/Seoul')),
        status LowCardinality(String),
        params_json String,
        error Nullable(String)
    )
    ENGINE = MergeTree
    PARTITION BY toYYYYMM(started_at)
    ORDER BY (job_name, provider, started_at, run_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS store.raw_api_events
    (
        run_id UUID,
        provider LowCardinality(String),
        endpoint String,
        request_hash FixedString(64),
        requested_at DateTime64(3, 'Asia/Seoul'),
        response_status UInt16,
        payload_json String
    )
    ENGINE = MergeTree
    PARTITION BY toYYYYMM(requested_at)
    ORDER BY (provider, endpoint, requested_at, request_hash)
    """,
    """
    CREATE TABLE IF NOT EXISTS market.daily_universe_members
    (
        trade_date Date,
        provider LowCardinality(String),
        universe_name LowCardinality(String),
        universe_type LowCardinality(String),
        universe_params_json String,
        market_country LowCardinality(String),
        rank Nullable(UInt16),
        symbol String,
        name String,
        selection_metric_name Nullable(String),
        selection_metric_value Nullable(Decimal(24, 4)),
        run_id UUID,
        collected_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(collected_at)
    PARTITION BY toYYYYMM(trade_date)
    ORDER BY (trade_date, provider, universe_name, symbol)
    """,
    """
    CREATE TABLE IF NOT EXISTS market.daily_ohlcv
    (
        trade_date Date,
        provider LowCardinality(String),
        symbol String,
        open Decimal(18, 4),
        high Decimal(18, 4),
        low Decimal(18, 4),
        close Decimal(18, 4),
        volume UInt64,
        trading_amount Decimal(24, 4),
        run_id UUID,
        collected_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(collected_at)
    PARTITION BY toYYYYMM(trade_date)
    ORDER BY (symbol, provider, trade_date)
    """,
    """
    CREATE TABLE IF NOT EXISTS market.daily_investor_trading
    (
        trade_date Date,
        provider LowCardinality(String),
        symbol String,
        investor_type LowCardinality(String),
        buy_amount Decimal(24, 4),
        sell_amount Decimal(24, 4),
        net_buy_amount Decimal(24, 4),
        buy_volume UInt64,
        sell_volume UInt64,
        net_buy_volume Int64,
        run_id UUID,
        collected_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(collected_at)
    PARTITION BY toYYYYMM(trade_date)
    ORDER BY (symbol, provider, investor_type, trade_date)
    """,
    """
    CREATE TABLE IF NOT EXISTS market.daily_short_credit_lending
    (
        trade_date Date,
        provider LowCardinality(String),
        symbol String,
        short_selling_amount Nullable(Decimal(24, 4)),
        short_selling_volume Nullable(UInt64),
        credit_balance_amount Nullable(Decimal(24, 4)),
        credit_balance_volume Nullable(UInt64),
        lending_balance_amount Nullable(Decimal(24, 4)),
        lending_balance_volume Nullable(UInt64),
        run_id UUID,
        collected_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(collected_at)
    PARTITION BY toYYYYMM(trade_date)
    ORDER BY (symbol, provider, trade_date)
    """,
    """
    CREATE TABLE IF NOT EXISTS market.stock_master
    (
        provider LowCardinality(String),
        symbol String,
        name String,
        market Nullable(String),
        market_country LowCardinality(String),
        currency Nullable(String),
        security_type Nullable(String),
        updated_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY (provider, symbol)
    """,
    """
    CREATE TABLE IF NOT EXISTS market.market_calendar
    (
        market_country LowCardinality(String),
        trade_date Date,
        is_open UInt8,
        reason Nullable(String),
        provider LowCardinality(String),
        updated_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY (market_country, trade_date, provider)
    """,
    """
    CREATE TABLE IF NOT EXISTS market.exchange_rates
    (
        trade_date Date,
        provider LowCardinality(String),
        base_currency LowCardinality(String),
        quote_currency LowCardinality(String),
        rate Decimal(18, 8),
        run_id UUID,
        collected_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(collected_at)
    PARTITION BY toYYYYMM(trade_date)
    ORDER BY (base_currency, quote_currency, provider, trade_date)
    """,
    """
    CREATE TABLE IF NOT EXISTS market.indicator_definitions
    (
        indicator_id String,
        name_ko String,
        name_en String,
        domain LowCardinality(String),
        category LowCardinality(String),
        provider LowCardinality(String),
        source_series String,
        unit String,
        frequency LowCardinality(String),
        higher_is LowCardinality(String),
        importance UInt8,
        description_ko String,
        interpretation_ko String,
        source_url String,
        availability LowCardinality(String),
        updated_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY indicator_id
    """,
    """
    CREATE TABLE IF NOT EXISTS market.indicator_observations
    (
        period Date,
        indicator_id String,
        provider LowCardinality(String),
        value Float64,
        metadata_json String,
        run_id UUID,
        collected_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(collected_at)
    PARTITION BY toYYYYMM(period)
    ORDER BY (indicator_id, provider, period)
    """,
    """
    CREATE TABLE IF NOT EXISTS market.real_estate_metrics
    (
        metric_id String,
        name_ko String,
        name_en String,
        category LowCardinality(String),
        deal_type LowCardinality(String),
        unit String,
        frequency LowCardinality(String),
        higher_is LowCardinality(String),
        description_ko String,
        interpretation_ko String,
        provider LowCardinality(String),
        source_series String,
        source_url String,
        updated_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY metric_id
    """,
    """
    CREATE TABLE IF NOT EXISTS market.real_estate_observations
    (
        period Date,
        week_start Date,
        metric_id LowCardinality(String),
        region LowCardinality(String),
        region_tier LowCardinality(String),
        property_type LowCardinality(String),
        deal_type LowCardinality(String),
        frequency LowCardinality(String),
        unit String,
        provider LowCardinality(String),
        value Float64,
        metadata_json String,
        run_id UUID,
        collected_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(collected_at)
    PARTITION BY toYear(period)
    ORDER BY (metric_id, region, property_type, deal_type, period)
    """,
    """
    CREATE TABLE IF NOT EXISTS market.daily_research_reports
    (
        report_date Date,
        report_id UUID,
        model LowCardinality(String),
        status LowCardinality(String),
        title String,
        markdown String,
        summary_json String,
        source_snapshot_json String,
        prompt_version LowCardinality(String),
        generated_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(generated_at)
    PARTITION BY toYYYYMM(report_date)
    ORDER BY (report_date, report_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS market.daily_technical_features
    (
        trade_date Date,
        provider LowCardinality(String),
        symbol String,
        close Decimal(18, 4),
        volume UInt64,
        vwap Nullable(Decimal(18, 4)),
        ma_20 Nullable(Decimal(18, 4)),
        ma_50 Nullable(Decimal(18, 4)),
        ma_200 Nullable(Decimal(18, 4)),
        ema_20 Nullable(Decimal(18, 4)),
        ema_50 Nullable(Decimal(18, 4)),
        ema_200 Nullable(Decimal(18, 4)),
        weekly_ma_50 Nullable(Decimal(18, 4)),
        weekly_ma_200 Nullable(Decimal(18, 4)),
        atr_14 Nullable(Decimal(18, 4)),
        rsi_14 Nullable(Float64),
        rsi_divergence LowCardinality(String),
        return_1d Nullable(Float64),
        return_5d Nullable(Float64),
        return_20d Nullable(Float64),
        volume_ratio_20 Nullable(Float64),
        htf_trend LowCardinality(String),
        run_id UUID,
        collected_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(collected_at)
    PARTITION BY toYYYYMM(trade_date)
    ORDER BY (symbol, provider, trade_date)
    """,
    "ALTER TABLE market.daily_technical_features ADD COLUMN IF NOT EXISTS vwap Nullable(Decimal(18, 4)) AFTER volume",
    "ALTER TABLE market.daily_technical_features ADD COLUMN IF NOT EXISTS ema_20 Nullable(Decimal(18, 4)) AFTER ma_200",
    "ALTER TABLE market.daily_technical_features ADD COLUMN IF NOT EXISTS ema_50 Nullable(Decimal(18, 4)) AFTER ema_20",
    "ALTER TABLE market.daily_technical_features ADD COLUMN IF NOT EXISTS ema_200 Nullable(Decimal(18, 4)) AFTER ema_50",
    "ALTER TABLE market.daily_technical_features ADD COLUMN IF NOT EXISTS rsi_14 Nullable(Float64) AFTER atr_14",
    "ALTER TABLE market.daily_technical_features ADD COLUMN IF NOT EXISTS rsi_divergence LowCardinality(String) DEFAULT 'none' AFTER rsi_14",
    """
    CREATE TABLE IF NOT EXISTS market.daily_volume_profile_levels
    (
        trade_date Date,
        provider LowCardinality(String),
        symbol String,
        lookback_days UInt16,
        source LowCardinality(String),
        poc_price Nullable(Decimal(18, 4)),
        value_area_low Nullable(Decimal(18, 4)),
        value_area_high Nullable(Decimal(18, 4)),
        bins_json String,
        run_id UUID,
        collected_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(collected_at)
    PARTITION BY toYYYYMM(trade_date)
    ORDER BY (symbol, provider, lookback_days, source, trade_date)
    """,
    """
    CREATE TABLE IF NOT EXISTS market.symbol_sentiment_items
    (
        symbol String,
        provider LowCardinality(String),
        query String,
        title String,
        source Nullable(String),
        url String,
        published_at Nullable(DateTime64(3, 'Asia/Seoul')),
        summary String,
        sentiment_label LowCardinality(String),
        sentiment_score Float64,
        sentiment_reason String,
        raw_json String,
        run_id UUID,
        collected_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(collected_at)
    PARTITION BY toYYYYMM(collected_at)
    ORDER BY (symbol, provider, url)
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio.paper_accounts
    (
        account_id String,
        name String,
        base_currency LowCardinality(String),
        initial_cash Decimal(24, 4),
        is_active UInt8,
        created_at DateTime64(3, 'Asia/Seoul'),
        updated_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY account_id
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio.paper_strategies
    (
        strategy_id String,
        name String,
        strategy_type LowCardinality(String),
        config_json String,
        is_active UInt8,
        created_at DateTime64(3, 'Asia/Seoul'),
        updated_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY strategy_id
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio.paper_backtest_runs
    (
        run_id UUID,
        account_id String,
        strategy_id String,
        start_date Date,
        end_date Date,
        initial_cash Decimal(24, 4),
        final_equity Decimal(24, 4),
        total_return Float64,
        max_drawdown Float64,
        trade_count UInt32,
        win_rate Float64,
        params_json String,
        created_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(created_at)
    PARTITION BY toYYYYMM(created_at)
    ORDER BY (account_id, strategy_id, run_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio.paper_backtest_daily_equity
    (
        run_id UUID,
        trade_date Date,
        cash Decimal(24, 4),
        positions_value Decimal(24, 4),
        equity Decimal(24, 4),
        drawdown Float64,
        created_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(created_at)
    PARTITION BY toYYYYMM(trade_date)
    ORDER BY (run_id, trade_date)
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio.paper_backtest_trades
    (
        run_id UUID,
        trade_date Date,
        symbol String,
        side LowCardinality(String),
        quantity UInt64,
        price Decimal(18, 4),
        gross_amount Decimal(24, 4),
        fee Decimal(24, 4),
        realized_pnl Decimal(24, 4),
        reason String,
        created_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = MergeTree
    PARTITION BY toYYYYMM(trade_date)
    ORDER BY (run_id, trade_date, symbol, side)
    """,
    """
    CREATE TABLE IF NOT EXISTS market.daily_pattern_events
    (
        trade_date Date,
        provider LowCardinality(String),
        symbol String,
        pattern_name LowCardinality(String),
        pattern_category LowCardinality(String),
        direction LowCardinality(String),
        detection_date Date,
        breakout_date Nullable(Date),
        retest_date Nullable(Date),
        neckline_price Nullable(Decimal(18, 4)),
        support_price Nullable(Decimal(18, 4)),
        resistance_price Nullable(Decimal(18, 4)),
        stop_price Nullable(Decimal(18, 4)),
        target_price Nullable(Decimal(18, 4)),
        confidence_score Float64,
        htf_trend LowCardinality(String),
        volume_profile_confluence UInt8,
        retest_confirmed UInt8,
        confluence_score Float64,
        poc_price Nullable(Decimal(18, 4)),
        value_area_low Nullable(Decimal(18, 4)),
        value_area_high Nullable(Decimal(18, 4)),
        feature_json String,
        run_id UUID,
        collected_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(collected_at)
    PARTITION BY toYYYYMM(trade_date)
    ORDER BY (symbol, provider, pattern_name, detection_date, trade_date)
    """,
    "ALTER TABLE market.daily_pattern_events ADD COLUMN IF NOT EXISTS htf_trend LowCardinality(String) AFTER confidence_score",
    "ALTER TABLE market.daily_pattern_events ADD COLUMN IF NOT EXISTS volume_profile_confluence UInt8 AFTER htf_trend",
    "ALTER TABLE market.daily_pattern_events ADD COLUMN IF NOT EXISTS retest_confirmed UInt8 AFTER volume_profile_confluence",
    "ALTER TABLE market.daily_pattern_events ADD COLUMN IF NOT EXISTS confluence_score Float64 AFTER retest_confirmed",
    "ALTER TABLE market.daily_pattern_events ADD COLUMN IF NOT EXISTS poc_price Nullable(Decimal(18, 4)) AFTER confluence_score",
    "ALTER TABLE market.daily_pattern_events ADD COLUMN IF NOT EXISTS value_area_low Nullable(Decimal(18, 4)) AFTER poc_price",
    "ALTER TABLE market.daily_pattern_events ADD COLUMN IF NOT EXISTS value_area_high Nullable(Decimal(18, 4)) AFTER value_area_low",
    """
    CREATE TABLE IF NOT EXISTS market.daily_pattern_outcomes
    (
        detection_date Date,
        provider LowCardinality(String),
        symbol String,
        pattern_name LowCardinality(String),
        horizon_days UInt16,
        entry_price Decimal(18, 4),
        stop_price Nullable(Decimal(18, 4)),
        target_price Nullable(Decimal(18, 4)),
        max_high Decimal(18, 4),
        min_low Decimal(18, 4),
        close_at_horizon Nullable(Decimal(18, 4)),
        target_hit UInt8,
        stop_hit UInt8,
        return_at_horizon Nullable(Float64),
        run_id UUID,
        collected_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(collected_at)
    PARTITION BY toYYYYMM(detection_date)
    ORDER BY (symbol, provider, pattern_name, horizon_days, detection_date)
    """,
    """
    CREATE TABLE IF NOT EXISTS dart.daily_disclosures
    (
        rcept_dt Date,
        corp_code String,
        stock_code Nullable(String),
        corp_name String,
        report_nm String,
        rcept_no String,
        flr_nm String,
        disclosure_type Nullable(String),
        raw_json String,
        run_id UUID,
        collected_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(collected_at)
    PARTITION BY toYYYYMM(rcept_dt)
    ORDER BY (rcept_dt, corp_code, rcept_no)
    """,
    """
    CREATE TABLE IF NOT EXISTS dart.symbol_corp_map
    (
        symbol String,
        corp_code Nullable(String),
        corp_name Nullable(String),
        source LowCardinality(String),
        updated_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY symbol
    """,
    """
    CREATE VIEW IF NOT EXISTS market.pattern_performance_summary AS
    SELECT
        o.provider AS provider,
        o.pattern_name AS pattern_name,
        e.pattern_category AS pattern_category,
        o.horizon_days AS horizon_days,
        e.htf_trend AS htf_trend,
        e.volume_profile_confluence AS volume_profile_confluence,
        e.retest_confirmed AS retest_confirmed,
        count() AS sample_count,
        avg(o.target_hit) AS target_hit_rate,
        avg(o.stop_hit) AS stop_hit_rate,
        avg(o.return_at_horizon) AS avg_return_at_horizon,
        quantile(0.5)(o.return_at_horizon) AS median_return_at_horizon,
        avg(e.confluence_score) AS avg_confluence_score
    FROM market.daily_pattern_outcomes AS o
    ANY LEFT JOIN market.daily_pattern_events AS e
        ON o.provider = e.provider
        AND o.symbol = e.symbol
        AND o.pattern_name = e.pattern_name
        AND o.detection_date = e.detection_date
    GROUP BY
        provider,
        pattern_name,
        pattern_category,
        horizon_days,
        htf_trend,
        volume_profile_confluence,
        retest_confirmed
    """,
    """
    CREATE VIEW IF NOT EXISTS market.symbol_sentiment_summary AS
    SELECT
        symbol,
        count() AS item_count,
        round(avg(sentiment_score), 4) AS avg_sentiment_score,
        multiIf(avg(sentiment_score) > 0.15, 'bullish', avg(sentiment_score) < -0.15, 'bearish', 'neutral')
            AS sentiment_label,
        anyLast(sentiment_reason) AS latest_reason,
        max(coalesce(published_at, collected_at)) AS latest_at
    FROM market.symbol_sentiment_items
    GROUP BY symbol
    """,
)

_TABLE_NAME_PATTERN = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-zA-Z0-9_.]+)")


def table_names() -> tuple[str, ...]:
    names: list[str] = []
    for statement in SCHEMA_STATEMENTS:
        match = _TABLE_NAME_PATTERN.search(statement)
        if match:
            names.append(match.group(1))
    return tuple(names)
