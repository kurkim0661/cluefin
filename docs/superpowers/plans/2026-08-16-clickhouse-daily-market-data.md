# ClickHouse Daily Market Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first `cluefin-store` package for daily universe storage, ClickHouse schema initialization, and daily pattern-analysis feature generation.

**Architecture:** Add a focused Python workspace package under `packages/cluefin-store`. The package owns ClickHouse DDL, typed daily market records, pattern-analysis calculations, and a small CLI. Provider-specific live ingestion remains behind protocols so DART/Toss/KIS adapters can be added without changing table contracts.

**Tech Stack:** Python 3.10, `uv` workspace, ClickHouse SQL DDL, `clickhouse-connect`, Click, pytest, pandas/numpy-free core calculations for easy unit testing.

---

### Task 1: Workspace Package Skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `packages/cluefin-store/pyproject.toml`
- Create: `packages/cluefin-store/src/cluefin_store/__init__.py`
- Create: `packages/cluefin-store/tests/test_import.py`

- [ ] **Step 1: Write failing import test**

Create `packages/cluefin-store/tests/test_import.py`:

```python
from cluefin_store import __version__


def test_import_exposes_version() -> None:
    assert __version__ == "0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/cluefin-store/tests/test_import.py -v`

Expected: FAIL because `cluefin_store` is not importable.

- [ ] **Step 3: Add package files**

Add `packages/cluefin-store/pyproject.toml`:

```toml
[project]
name = "cluefin-store"
version = "0.1.0"
description = "ClickHouse-backed daily market data store for Cluefin"
requires-python = ">=3.10"
dependencies = [
    "click>=8.1.7",
    "clickhouse-connect>=0.10.0",
    "pydantic>=2.12.0,<3.0.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project.scripts]
cluefin-store = "cluefin_store.cli:cli"

[tool.ruff]
extend = "../../pyproject.toml"
include = ["src/**", "tests/**"]
```

Add `packages/cluefin-store/src/cluefin_store/__init__.py`:

```python
__version__ = "0.1.0"
```

Update root `pyproject.toml`:

```toml
[tool.uv.workspace]
members = [
    "packages/cluefin-openapi",
    "packages/cluefin-ta",
    "packages/cluefin-xbrl",
    "packages/cluefin-store",
    "apps/cluefin-cli",
    "apps/cluefin-openapi-cli",
    "apps/cluefin-desk",
]

[tool.uv.sources]
cluefin-store = { workspace = true }
```

Also add `"packages/cluefin-store/tests"` to `tool.pytest.ini_options.testpaths` and `"packages/cluefin-store/src"` to `tool.coverage.run.source`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv sync --all-packages && uv run pytest packages/cluefin-store/tests/test_import.py -v`

Expected: PASS.

### Task 2: ClickHouse Schema Module

**Files:**
- Create: `packages/cluefin-store/src/cluefin_store/schema.py`
- Create: `packages/cluefin-store/tests/test_schema.py`

- [ ] **Step 1: Write failing schema tests**

Create `packages/cluefin-store/tests/test_schema.py`:

```python
from cluefin_store.schema import SCHEMA_STATEMENTS, table_names


def test_schema_includes_core_databases_and_tables() -> None:
    sql = "\n".join(SCHEMA_STATEMENTS)

    assert "CREATE DATABASE IF NOT EXISTS store" in sql
    assert "CREATE DATABASE IF NOT EXISTS market" in sql
    assert "CREATE DATABASE IF NOT EXISTS dart" in sql
    assert "CREATE TABLE IF NOT EXISTS store.ingest_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS market.daily_universe_members" in sql
    assert "CREATE TABLE IF NOT EXISTS market.daily_pattern_events" in sql
    assert "CREATE TABLE IF NOT EXISTS market.daily_pattern_outcomes" in sql


def test_table_names_are_extracted_from_schema() -> None:
    assert "market.daily_ohlcv" in table_names()
    assert "market.daily_volume_profile_levels" in table_names()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/cluefin-store/tests/test_schema.py -v`

Expected: FAIL because `cluefin_store.schema` is missing.

- [ ] **Step 3: Implement schema module**

Create `schema.py` with `SCHEMA_STATEMENTS` containing DDL from the approved spec and a `table_names()` helper that parses `CREATE TABLE IF NOT EXISTS <name>`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/cluefin-store/tests/test_schema.py -v`

Expected: PASS.

### Task 3: Typed Daily Records

**Files:**
- Create: `packages/cluefin-store/src/cluefin_store/models.py`
- Create: `packages/cluefin-store/tests/test_models.py`

- [ ] **Step 1: Write failing model tests**

Create `packages/cluefin-store/tests/test_models.py`:

```python
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from cluefin_store.models import DailyOhlcv, DailyUniverseMember


def test_daily_universe_member_serializes_for_clickhouse() -> None:
    row = DailyUniverseMember(
        trade_date=date(2026, 8, 14),
        provider="toss",
        universe_name="manual_005930",
        universe_type="symbols",
        universe_params_json='{"symbols":["005930"]}',
        market_country="KR",
        rank=None,
        symbol="005930",
        name="삼성전자",
        selection_metric_name=None,
        selection_metric_value=None,
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        collected_at=datetime(2026, 8, 14, 16, 0, 0),
    )

    assert row.as_insert_row()["symbol"] == "005930"
    assert row.as_insert_row()["rank"] is None


def test_daily_ohlcv_accepts_decimal_prices() -> None:
    row = DailyOhlcv(
        trade_date=date(2026, 8, 14),
        provider="toss",
        symbol="005930",
        open=Decimal("100.0"),
        high=Decimal("110.0"),
        low=Decimal("95.0"),
        close=Decimal("108.0"),
        volume=1000,
        trading_amount=Decimal("108000.0"),
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        collected_at=datetime(2026, 8, 14, 16, 0, 0),
    )

    assert row.typical_price == Decimal("104.3333")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/cluefin-store/tests/test_models.py -v`

Expected: FAIL because `cluefin_store.models` is missing.

- [ ] **Step 3: Implement models**

Use dataclasses for `DailyUniverseMember`, `DailyOhlcv`, `TechnicalFeature`, `VolumeProfileLevel`, `PatternEvent`, and `PatternOutcome`. Each model exposes `as_insert_row()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/cluefin-store/tests/test_models.py -v`

Expected: PASS.

### Task 4: Pattern Feature and Outcome Calculations

**Files:**
- Create: `packages/cluefin-store/src/cluefin_store/patterns.py`
- Create: `packages/cluefin-store/tests/test_patterns.py`

- [ ] **Step 1: Write failing pattern tests**

Create `packages/cluefin-store/tests/test_patterns.py` with tests for:

- 50-day moving average feature generation.
- Daily volume-profile approximation POC.
- Double-bottom detection on a synthetic series.
- Pattern outcome target-hit labeling.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/cluefin-store/tests/test_patterns.py -v`

Expected: FAIL because `cluefin_store.patterns` is missing.

- [ ] **Step 3: Implement pattern helpers**

Implement:

- `compute_technical_features(candles, provider, run_id, collected_at)`
- `approximate_volume_profile(candles, provider, run_id, collected_at, lookback_days=60, bin_count=24)`
- `detect_pattern_events(candles, provider, run_id, collected_at)`
- `label_pattern_outcome(event, future_candles, horizon_days, run_id, collected_at)`

Detectors should initially support `double_bottom`, `inverse_head_and_shoulders`, `ascending_triangle`, and `high_tight_flag` with conservative deterministic heuristics. They must emit feature JSON rather than success claims.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/cluefin-store/tests/test_patterns.py -v`

Expected: PASS.

### Task 5: ClickHouse Store and CLI

**Files:**
- Create: `packages/cluefin-store/src/cluefin_store/db.py`
- Create: `packages/cluefin-store/src/cluefin_store/cli.py`
- Create: `packages/cluefin-store/tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create tests using `click.testing.CliRunner`:

- `cluefin-store doctor --dry-run` prints configured host and database.
- `cluefin-store init --dry-run` prints DDL and does not connect.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/cluefin-store/tests/test_cli.py -v`

Expected: FAIL because CLI is missing.

- [ ] **Step 3: Implement DB and CLI**

`db.py` defines `ClickHouseSettings` and `ClickHouseStore.apply_schema()`. `cli.py` defines `doctor` and `init` commands with `--dry-run`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/cluefin-store/tests/test_cli.py -v`

Expected: PASS.

### Task 6: Verification

**Files:**
- Modify: `uv.lock`
- Modify: docs/spec if implementation names changed during execution.

- [ ] **Step 1: Run focused package tests**

Run: `uv run pytest packages/cluefin-store/tests -v`

Expected: PASS.

- [ ] **Step 2: Run full fast Python test suite**

Run: `uv run pytest -m "not integration and not slow"`

Expected: PASS.

- [ ] **Step 3: Run formatting and lint**

Run: `uv run ruff format packages/cluefin-store && uv run ruff check packages/cluefin-store`

Expected: PASS with no remaining fixes.

- [ ] **Step 4: Commit**

Commit with:

```bash
git add pyproject.toml uv.lock packages/cluefin-store docs/superpowers
git commit -m "feat(store): ClickHouse 일별 유니버스 저장소 추가"
```
