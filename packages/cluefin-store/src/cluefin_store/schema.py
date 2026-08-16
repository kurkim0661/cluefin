from __future__ import annotations

import re

SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE DATABASE IF NOT EXISTS store",
    "CREATE DATABASE IF NOT EXISTS market",
    "CREATE DATABASE IF NOT EXISTS dart",
    """
    CREATE TABLE IF NOT EXISTS store.universe_definitions
    (
        universe_name LowCardinality(String),
        universe_type LowCardinality(String),
        provider Nullable(LowCardinality(String)),
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
        selection_metric_name Nullable(LowCardinality(String)),
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
        market Nullable(LowCardinality(String)),
        market_country LowCardinality(String),
        currency Nullable(LowCardinality(String)),
        security_type Nullable(LowCardinality(String)),
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
    CREATE TABLE IF NOT EXISTS market.daily_technical_features
    (
        trade_date Date,
        provider LowCardinality(String),
        symbol String,
        close Decimal(18, 4),
        volume UInt64,
        ma_20 Nullable(Decimal(18, 4)),
        ma_50 Nullable(Decimal(18, 4)),
        ma_200 Nullable(Decimal(18, 4)),
        weekly_ma_50 Nullable(Decimal(18, 4)),
        weekly_ma_200 Nullable(Decimal(18, 4)),
        atr_14 Nullable(Decimal(18, 4)),
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
        feature_json String,
        run_id UUID,
        collected_at DateTime64(3, 'Asia/Seoul')
    )
    ENGINE = ReplacingMergeTree(collected_at)
    PARTITION BY toYYYYMM(trade_date)
    ORDER BY (symbol, provider, pattern_name, detection_date, trade_date)
    """,
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
)

_TABLE_NAME_PATTERN = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-zA-Z0-9_.]+)")


def table_names() -> tuple[str, ...]:
    names: list[str] = []
    for statement in SCHEMA_STATEMENTS:
        match = _TABLE_NAME_PATTERN.search(statement)
        if match:
            names.append(match.group(1))
    return tuple(names)
