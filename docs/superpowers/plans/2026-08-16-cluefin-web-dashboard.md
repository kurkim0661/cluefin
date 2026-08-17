# Cluefin Web Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a browser dashboard that surfaces ClickHouse-backed Korean market pattern signals and preserves useful `cluefin-desk` workflows.

**Architecture:** Add `apps/cluefin-web` as a Python FastAPI app in the existing `uv` workspace. The backend reads `cluefin-store` ClickHouse tables through a small repository layer and serves both JSON APIs and a static dashboard shell. The frontend uses plain HTML/CSS/JS so it can run with `uv run cluefin-web` without a separate JS build.

**Tech Stack:** Python 3.10, FastAPI, Uvicorn, ClickHouse via `cluefin-store`, pytest, vanilla HTML/CSS/JS.

---

### Task 1: Web Package Skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `apps/cluefin-web/pyproject.toml`
- Create: `apps/cluefin-web/src/cluefin_web/__init__.py`
- Create: `apps/cluefin-web/src/cluefin_web/app.py`
- Create: `apps/cluefin-web/tests/test_app.py`

- [ ] **Step 1: Write failing tests**

Test that `create_app()` returns a FastAPI app and `/healthz` returns `{"status": "ok"}`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/cluefin-web/tests/test_app.py -v`

Expected: FAIL because `cluefin_web` is not importable.

- [ ] **Step 3: Implement package skeleton**

Add the workspace member, package metadata, `create_app()`, and script entrypoint `cluefin-web`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv sync --all-packages && uv run pytest apps/cluefin-web/tests/test_app.py -v`

Expected: PASS.

### Task 2: Dashboard Data Repository

**Files:**
- Create: `apps/cluefin-web/src/cluefin_web/repository.py`
- Create: `apps/cluefin-web/tests/test_repository.py`

- [ ] **Step 1: Write failing tests**

Use a fake ClickHouse client to verify repository methods return pattern performance rows, latest signals, market overview metrics, and empty lists when tables are empty.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/cluefin-web/tests/test_repository.py -v`

Expected: FAIL because `repository.py` is missing.

- [ ] **Step 3: Implement repository**

Create `DashboardRepository` with `pattern_performance()`, `latest_signals()`, `market_overview()`, and `snapshot()`. Keep SQL centralized and parameterized.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest apps/cluefin-web/tests/test_repository.py -v`

Expected: PASS.

### Task 3: API and Dashboard Shell

**Files:**
- Modify: `apps/cluefin-web/src/cluefin_web/app.py`
- Create: `apps/cluefin-web/src/cluefin_web/templates/index.html`
- Create: `apps/cluefin-web/src/cluefin_web/static/app.css`
- Create: `apps/cluefin-web/src/cluefin_web/static/app.js`
- Modify: `apps/cluefin-web/tests/test_app.py`

- [ ] **Step 1: Write failing tests**

Test `/`, `/api/dashboard`, `/api/pattern-performance`, and `/api/signals` with a fake repository.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/cluefin-web/tests/test_app.py -v`

Expected: FAIL because routes are missing.

- [ ] **Step 3: Implement routes and assets**

Serve a dense dashboard shell with summary metrics, signal table, pattern-performance table, market panel, and watchlist/search controls. The JSON endpoints return the same repository data used by the frontend.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest apps/cluefin-web/tests/test_app.py -v`

Expected: PASS.

### Task 4: Verification and Run

**Files:**
- Modify: `uv.lock`

- [ ] **Step 1: Run web tests**

Run: `uv run pytest apps/cluefin-web/tests -v`

Expected: PASS.

- [ ] **Step 2: Run workspace fast tests**

Run: `uv run pytest -m "not integration and not slow"`

Expected: PASS.

- [ ] **Step 3: Run lint and format checks**

Run: `uv run ruff check apps/cluefin-web packages/cluefin-store && uv run ruff format --check apps/cluefin-web packages/cluefin-store`

Expected: PASS.

- [ ] **Step 4: Start local server**

Run: `uv run cluefin-web --host 127.0.0.1 --port 8765`

Expected: Server starts and `GET /healthz` returns `{"status":"ok"}`.
