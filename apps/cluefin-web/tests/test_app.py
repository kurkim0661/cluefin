from cluefin_web.app import PACKAGE_DIR, create_app


class FakeRepository:
    client = "fake-clickhouse-client"

    def __init__(self) -> None:
        self.saved: list[dict] = []
        self.ran: list[dict] = []
        self.deleted: list[str] = []

    def snapshot(self) -> dict:
        return {
            "overview": {"symbols": 1},
            "performance": [],
            "signals": [],
            "universe": [],
            "technicals": [],
            "sentiment": [],
        }

    def market_pulse(self) -> dict:
        return {
            "regime": {"key": "mixed", "label": "혼조", "headline": "지표 혼조", "summary": "확인 필요", "score": 0},
            "coverage": {"observed": 1, "total": 2, "fresh": 1, "public_missing": 0, "connection_required": 1},
            "sections": {},
            "drivers": [],
            "indicators": [],
            "unavailable": [],
        }

    def latest_research_report(self) -> dict | None:
        return None

    def research_report_history(self, limit: int = 30) -> list[dict]:
        return [
            {
                "report_date": "2026-08-27",
                "report_id": "report-1",
                "model": "stub",
                "status": "validated",
                "title": "오늘의 시장 리포트",
                "prompt_version": "cluefin-daily-v1",
                "generated_at": "2026-08-27 09:00:00",
                "markdown_chars": 1200,
            }
        ][:limit]

    def research_report(self, report_id: str) -> dict | None:
        if report_id != "report-1":
            return None
        return {
            "report_date": "2026-08-27",
            "report_id": "report-1",
            "model": "stub",
            "status": "validated",
            "title": "오늘의 시장 리포트",
            "markdown": "# 오늘의 시장 리포트",
            "summary_json": "{}",
            "prompt_version": "cluefin-daily-v1",
            "generated_at": "2026-08-27 09:00:00",
        }

    def sql_schema(self, *, refresh: bool = False) -> dict:
        return {
            "database": "market",
            "max_rows": 2000,
            "fetched_at": "2026-08-31T20:00:00",
            "tables": [
                {
                    "database": "market",
                    "table": "real_estate_observations",
                    "engine": "ReplacingMergeTree",
                    "sorting_key": "metric_id, period",
                    "total_rows": 15216,
                    "columns": [
                        {"name": "period", "type": "Date", "role": "time", "comment": ""},
                        {"name": "metric_id", "type": "LowCardinality(String)", "role": "dimension", "comment": ""},
                        {"name": "value", "type": "Float64", "role": "fact", "comment": ""},
                    ],
                    "samples": {"metric_id": ["unsold_housing"]},
                    "period": {"column": "period", "first": "2016-09-01", "last": "2026-06-01"},
                    "catalog": [{"key": "unsold_housing", "label": "미분양 주택"}],
                }
            ],
        }

    def sql_run(self, payload: dict) -> dict:
        if "drop" in str(payload.get("sql", "")).lower():
            raise ValueError("읽기 전용 콘솔이라 SELECT로 시작하는 문장만 실행할 수 있습니다.")
        self.ran.append(payload)
        return {
            "sql": payload.get("sql", ""),
            "columns": ["기간", "평균"],
            "column_types": ["Date", "Float64"],
            "rows": [["2026-06-01", 109.5]],
            "row_count": 1,
            "truncated": False,
            "elapsed_ms": 4.2,
        }

    def saved_sql_queries(self, limit: int = 100) -> list[dict]:
        return self.saved[:limit]

    def save_sql_query(self, payload: dict) -> dict:
        if not payload.get("name"):
            raise ValueError("쿼리 이름을 입력해 주세요.")
        row = {
            "name": payload["name"],
            "query_id": "00000000-0000-0000-0000-000000000001",
            "question": payload.get("question", ""),
            "sql": payload.get("sql", ""),
            "note": "",
            "source": payload.get("source", "manual"),
            "created_at": "2026-08-31T20:00:00",
            "updated_at": "2026-08-31T20:00:00",
        }
        self.saved = [item for item in self.saved if item["name"] != row["name"]] + [row]
        return row

    def delete_sql_query(self, name: str) -> dict:
        self.deleted.append(name)
        self.saved = [item for item in self.saved if item["name"] != name]
        return {"name": name, "deleted": True}

    def real_estate_meta(self) -> dict:
        return {
            "metrics": [{"metric_id": "house_sale_price_index", "name_ko": "주택 매매가격지수", "unit": "2026.01=100"}],
            "combinations": [],
            "dimensions": {"region": ["서울", "경기"], "property_type": ["아파트"]},
            "coverage": {"observations": 2208, "metrics": 7, "regions": 11},
            "templates": [
                {
                    "id": "capital_sale_index",
                    "name": "수도권 아파트 매매가격지수",
                    "metric_ids": ["house_sale_price_index"],
                }
            ],
        }

    def real_estate_query(self, payload: dict) -> dict:
        if not payload.get("metric_ids"):
            raise ValueError("metric_ids는 최소 1개가 필요합니다.")
        return {
            "buckets": ["2026-05-01", "2026-06-01"],
            "series": [{"name": "서울", "points": [102.7, 104.0]}],
            "unit": "2026.01=100",
            "transform": payload.get("transform", "raw"),
            "transform_label": "원값",
            "bucket": payload.get("bucket", "month"),
            "aggregation": "avg",
            "series_dimension": payload.get("series_dimension", "region"),
            "metric_ids": payload["metric_ids"],
        }

    def pattern_performance(self) -> list[dict]:
        return [{"pattern_name": "double_bottom"}]

    def latest_signals(self) -> list[dict]:
        return [{"symbol": "005930"}]

    def universe_members(self) -> list[dict]:
        return [{"symbol": "005930", "name": "삼성전자"}]

    def latest_technicals(self) -> list[dict]:
        return [{"symbol": "005930", "rsi_divergence": "bullish"}]

    def latest_sentiment(self) -> list[dict]:
        return [{"symbol": "005930", "sentiment_label": "bullish"}]

    def symbol_chart(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "candles": [{"trade_date": "2026-08-16", "close": 70000}],
            "patterns": [],
            "pattern_candidates": [{"pattern_name": "falling_wedge", "completion_score": 0.83}],
            "sentiment_items": [{"title": "삼성전자 수주", "url": "https://example.com/news"}],
        }

    def paper_dashboard(self) -> dict:
        return {
            "accounts": [{"account_id": "paper-1", "name": "Pattern Lab"}],
            "strategies": [{"strategy_id": "strategy-1", "name": "Confluence"}],
            "backtests": [{"run_id": "run-1", "total_return": 0.12}],
        }

    def create_paper_account(self, payload: dict) -> dict:
        return {"account_id": "paper-1", "name": payload["name"]}

    def create_paper_strategy(self, payload: dict) -> dict:
        return {"strategy_id": "strategy-1", "name": payload["name"]}

    def run_paper_backtest(self, payload: dict) -> dict:
        return {"run_id": "run-1", "total_return": 0.12, "years": payload.get("years", 1)}


def test_healthz_returns_ok() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(FakeRepository()))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_routes_return_html_and_json() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(FakeRepository()))

    assert client.get("/").status_code == 200
    assert client.get("/api/dashboard").json()["overview"]["symbols"] == 1
    assert client.get("/api/market-pulse").json()["regime"]["label"] == "혼조"
    assert client.get("/api/research-report").json() is None
    assert client.get("/api/pattern-performance").json()[0]["pattern_name"] == "double_bottom"
    assert client.get("/api/signals").json()[0]["symbol"] == "005930"
    assert client.get("/api/universe").json()[0]["name"] == "삼성전자"
    assert client.get("/api/technicals").json()[0]["rsi_divergence"] == "bullish"
    assert client.get("/api/sentiment").json()[0]["sentiment_label"] == "bullish"
    assert client.get("/api/symbols/005930/chart").json()["symbol"] == "005930"
    assert client.get("/api/paper").json()["accounts"][0]["name"] == "Pattern Lab"
    assert client.post("/api/paper/accounts", json={"name": "Pattern Lab"}).json()["account_id"] == "paper-1"
    assert client.post("/api/paper/strategies", json={"name": "Confluence"}).json()["strategy_id"] == "strategy-1"
    assert (
        client.post(
            "/api/paper/backtests", json={"account_id": "paper-1", "strategy_id": "strategy-1", "years": 1}
        ).json()["total_return"]
        == 0.12
    )


def test_research_report_history_and_detail_routes() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(FakeRepository()))

    history = client.get("/api/research-reports").json()
    assert history[0]["report_id"] == "report-1"
    assert "markdown" not in history[0]
    assert client.get("/api/research-reports/report-1").json()["title"] == "오늘의 시장 리포트"
    assert client.get("/api/research-reports/unknown").status_code == 404


def test_generate_report_requires_llm_settings(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    for name in ("CLUEFIN_LLM_PAT", "CLUEFIN_LLM_BASE_URL", "CLUEFIN_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(FakeRepository()))

    status = client.get("/api/research-reports/status").json()
    assert status["status"] == "idle"
    assert status["llm"]["configured"] is False

    response = client.post("/api/research-reports/generate", json={})

    assert response.status_code == 400
    assert "CLUEFIN_LLM_PAT" in response.json()["detail"]


def test_generate_report_runs_agent_and_reports_result(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from cluefin_web.report_jobs import ReportGenerationJob

    monkeypatch.setenv("CLUEFIN_LLM_PAT", "stub-pat")
    monkeypatch.setenv("CLUEFIN_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("CLUEFIN_LLM_MODEL", "stub-model")
    calls: list[str] = []

    def fake_runner(report_date):
        calls.append(report_date.isoformat())
        return {"report_status": "validated", "model": "stub-model", "persisted": True}

    job = ReportGenerationJob(runner=fake_runner)
    client = TestClient(create_app(FakeRepository(), report_job=job))

    accepted = client.post("/api/research-reports/generate", json={"report_date": "2026-08-27"})

    assert accepted.status_code == 200
    assert accepted.json()["report_date"] == "2026-08-27"
    job._thread.join(timeout=5)
    status = client.get("/api/research-reports/status").json()
    assert calls == ["2026-08-27"]
    assert status["status"] == "succeeded"
    assert "생성했습니다" in status["message"]


def test_generate_report_rejects_invalid_date(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CLUEFIN_LLM_PAT", "stub-pat")
    monkeypatch.setenv("CLUEFIN_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("CLUEFIN_LLM_MODEL", "stub-model")
    client = TestClient(create_app(FakeRepository()))

    assert client.post("/api/research-reports/generate", json={"report_date": "27-08-2026"}).status_code == 400


def test_dashboard_tabs_have_targets_and_click_handler() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(FakeRepository()))

    html = client.get("/").text
    script = (PACKAGE_DIR / "static" / "app.js").read_text(encoding="utf-8")

    for tab in ("pulse", "overview", "signals", "patterns", "universe", "paper"):
        assert f'data-tab="{tab}"' in html
        assert f'id="{tab}-panel"' in html
        assert f'aria-controls="{tab}-panel"' in html

    assert "setActiveTab" in script
    assert "renderMarketPulse" in script
    assert "renderDriverGrid" in script
    assert 'id="driver-grid"' in html
    assert 'querySelectorAll("[data-tab]")' in script
    assert "renderUniverse" in script
    assert "rsi_divergence" in script
    assert "renderCandlestickChart" in script
    assert "visibleChartCandles" in script
    assert "initializeChartInteractions" in script
    assert "handleChartWheel" in script
    assert "handleChartPointerMove" in script
    assert "applyYScale" in script
    assert "chartYScale" in script
    assert "chartOffset" in script
    assert "dragStartOffset" in script
    assert "sortTable" in script
    assert 'id="chart-window"' in html
    assert 'id="chart-y-zoom"' in html
    assert 'id="chart-y-reset"' in html
    assert 'data-indicator="ema_20"' in html
    assert 'id="signal-evidence"' in html
    assert "renderSignalEvidence" in script
    assert "sentiment_items" in script
    assert 'id="pattern-type-filters"' in html
    assert 'id="pattern-event-select"' in html
    assert "renderPatternControls" in script
    assert "selectedPatternEventKey" in script
    assert "drawPatternGeometry" in script
    assert "drawPatternCandidates" in script
    assert "pattern_candidates" in script
    assert "completion_score" in script
    assert "geometry" in script
    assert "drawVolumeProfileBars" in script
    assert 'id="paper-panel"' in html
    assert 'name="pattern_names"' in html
    assert 'name="selected_indicators"' in html
    assert 'name="min_indicator_agreement"' in html
    assert 'name="require_retest"' in html
    assert "formPayloadWithLists" in script
    assert "pattern_names" in script
    assert "selected_indicators" in script
    assert "loadPaperDashboard" in script
    assert "runPaperBacktest" in script
    assert 'id="pattern-card-grid"' in html
    assert 'id="universe-watchlist"' in html
    assert 'data-strategy-preset="balanced"' in html
    assert "applyStrategyPreset" in script
    assert "patternVerdict" in script
    assert "priorityLabel" in script
    assert 'id="indicator-modal"' in html
    assert "openIndicatorChart" in script
    assert "drawIndicatorSeriesChart" in script
    assert "initializeIndicatorSeriesInteractions" in script
    assert "data-money-input" in html
    assert 'value="10,000,000"' in html
    assert 'id="strategy-rule-summary"' in html
    assert 'name="stop_loss_pct"' in html
    assert 'name="take_profit_pct"' in html
    assert "renderStrategyRuleSummary" in script
    assert "koreanMoneyLabel" in script
    assert 'id="ai-report-content"' in html
    assert "renderResearchReport" in script
    assert 'id="ai-report-generate"' in html
    assert 'id="ai-report-history-list"' in html
    assert 'id="ai-report-job"' in html
    assert "generateResearchReport" in script
    assert "renderReportHistory" in script
    assert "openStoredReport" in script
    assert "pollReportJob" in script
    assert "inlineMarkdown" in script
    assert "skipLeadingTitle" in script
    assert "isTableSeparator" in script
    assert "ai-report-table" in script
    assert "reportCitedIndicators" in script
    assert "renderReportIndicatorStrip" in script
    assert "data-report-indicator" in script

    for indicator in ("vwap", "ema_20", "ema_50", "ema_200", "volume", "rsi", "profile", "patterns"):
        assert f'data-indicator="{indicator}" checked' not in html


def test_chart_has_independent_y_axis_range_controls() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(FakeRepository()))

    html = client.get("/").text
    script = (PACKAGE_DIR / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="chart-y-scale-value"' in html
    assert "isChartYAxisHit" in script
    assert "adjustChartYScale" in script
    assert "chartYDrag" in script
    assert "updateChartYScaleControl" in script


def test_crypto_indicator_help_reuses_bitcoin_concepts_for_eth_and_xrp() -> None:
    script = (PACKAGE_DIR / "static" / "app.js").read_text(encoding="utf-8")

    assert "CRYPTO_ASSET_LABELS" in script
    assert "cryptoConceptFallback" in script
    assert "이더리움" in script and "리플" in script


def test_real_estate_routes_expose_meta_and_query() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(FakeRepository()))

    meta = client.get("/api/real-estate/meta").json()
    assert meta["coverage"]["observations"] == 2208
    assert meta["templates"][0]["id"] == "capital_sale_index"

    result = client.post(
        "/api/real-estate/query",
        json={"metric_ids": ["house_sale_price_index"], "series_dimension": "region", "bucket": "month"},
    )

    assert result.status_code == 200
    assert result.json()["series"][0]["name"] == "서울"


def test_real_estate_query_rejects_empty_metric_selection() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(FakeRepository()))

    response = client.post("/api/real-estate/query", json={"metric_ids": []})

    assert response.status_code == 400
    assert "metric_ids" in response.json()["detail"]


def test_real_estate_tab_has_builder_and_template_targets() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(FakeRepository()))
    html = client.get("/").text
    script = (PACKAGE_DIR / "static" / "app.js").read_text(encoding="utf-8")

    assert 'data-tab="estate"' in html
    assert 'id="estate-panel"' in html
    assert 'id="estate-templates"' in html
    assert 'id="estate-metrics"' in html
    assert 'id="estate-series"' in html
    assert 'id="estate-chart-type"' in html
    for dimension in ("region", "region_tier", "property_type", "deal_type"):
        assert f'id="estate-filter-{dimension}"' in html
    assert "loadEstate" in script
    assert "applyEstateTemplate" in script
    assert "collectEstatePayload" in script
    assert "renderEstateChart" in script
    assert "ESTATE_FILTER_DIMENSIONS" in script


def test_real_estate_tab_can_overlay_price_index_on_indicator_charts() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(FakeRepository()))
    html = client.get("/").text
    script = (PACKAGE_DIR / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="estate-overlay"' in html
    assert 'id="estate-overlay-metric"' in html
    assert 'id="estate-overlay-region"' in html
    assert 'id="estate-overlay-note"' in html
    assert "collectEstateOverlay" in script
    assert "drawEstateOverlay" in script
    assert "estateOverlayLabel" in script
    # 실거래지수를 기준선으로 쓸 때는 아파트 매매 한정·신고 지연 수정을 함께 알린다.
    assert "신고 지연 때문에 최근 값은 나중에 수정될 수 있습니다" in script
    # 차트 컨테이너와 차트 형태 셀렉트가 같은 id를 쓰면 캔버스 크기가 0이 된다.
    assert html.count('id="estate-chart"') == 1


def test_sql_schema_route_exposes_columns_roles_and_llm_state() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(FakeRepository()))

    schema = client.get("/api/sql/schema").json()

    table = schema["tables"][0]
    assert schema["database"] == "market"
    assert schema["max_rows"] == 2000
    assert {column["name"]: column["role"] for column in table["columns"]} == {
        "period": "time",
        "metric_id": "dimension",
        "value": "fact",
    }
    assert table["catalog"] == [{"key": "unsold_housing", "label": "미분양 주택"}]
    # LLM 설정 여부는 화면이 자연어 입력창을 켤지 결정하는 데 쓴다.
    assert "configured" in schema["llm"]


def test_sql_run_route_returns_table_and_rejects_write_statements() -> None:
    from fastapi.testclient import TestClient

    repository = FakeRepository()
    client = TestClient(create_app(repository))

    ok = client.post("/api/sql/run", json={"sql": "SELECT 1"})
    assert ok.status_code == 200
    assert ok.json()["columns"] == ["기간", "평균"]
    assert repository.ran == [{"sql": "SELECT 1"}]

    blocked = client.post("/api/sql/run", json={"sql": "DROP TABLE market.real_estate_observations"})
    assert blocked.status_code == 400
    assert "읽기 전용" in blocked.json()["detail"]


def test_sql_translate_route_uses_injected_agent_and_maps_failures() -> None:
    from fastapi.testclient import TestClient

    calls: list[tuple] = []

    def translator(client_obj, question: str, *, schema_context=None) -> dict:
        calls.append((client_obj, question, bool(schema_context)))
        if question == "터짐":
            raise RuntimeError("gateway timeout")
        if not question:
            raise ValueError("무엇을 보고 싶은지 한 줄로 적어 주세요.")
        return {"sql": "SELECT 1 LIMIT 1", "explanation": "한 줄", "notes": [], "status": "validated", "attempts": 1}

    client = TestClient(create_app(FakeRepository(), sql_translator=translator))

    ok = client.post("/api/sql/translate", json={"question": "미분양 추이"})
    assert ok.status_code == 200
    assert ok.json()["sql"] == "SELECT 1 LIMIT 1"
    # 에이전트는 저장소의 ClickHouse 클라이언트와 스키마 컨텍스트를 함께 받는다.
    assert calls[0] == ("fake-clickhouse-client", "미분양 추이", True)

    assert client.post("/api/sql/translate", json={"question": ""}).status_code == 400
    assert client.post("/api/sql/translate", json={"question": "터짐"}).status_code == 502


def test_saved_sql_query_routes_round_trip() -> None:
    from fastapi.testclient import TestClient

    repository = FakeRepository()
    client = TestClient(create_app(repository))

    saved = client.post("/api/sql/saved", json={"name": "수도권 미분양", "sql": "SELECT 1", "source": "nl2sql"})
    assert saved.status_code == 200
    assert saved.json()["source"] == "nl2sql"
    assert [item["name"] for item in client.get("/api/sql/saved").json()] == ["수도권 미분양"]

    assert client.post("/api/sql/saved", json={"sql": "SELECT 1"}).status_code == 400

    assert client.delete("/api/sql/saved/수도권 미분양").json() == {"name": "수도권 미분양", "deleted": True}
    assert client.get("/api/sql/saved").json() == []
    assert repository.deleted == ["수도권 미분양"]


def test_sql_workbench_tab_has_schema_editor_and_agent_targets() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(FakeRepository()))
    html = client.get("/").text
    script = (PACKAGE_DIR / "static" / "app.js").read_text(encoding="utf-8")

    assert 'data-tab="sql"' in html
    for element in (
        "sql-panel",
        "sql-table",
        "sql-dimensions",
        "sql-facts",
        "sql-question",
        "sql-text",
        "sql-result",
        "sql-saved",
    ):
        assert f'id="{element}"' in html
    assert "loadSqlWorkbench" in script
    assert "buildSqlSkeleton" in script
    assert "translateSqlQuestion" in script
    assert "runSqlQuery" in script
    # 한글 별칭은 backtick으로 감싸야 ClickHouse가 받는다.
    assert "sqlIdentifier" in script


def test_create_app_loads_llm_settings_from_the_env_file(monkeypatch, tmp_path) -> None:
    """.env에만 CLUEFIN_LLM_*를 적어 둔 상태로 웹을 띄워도 자연어 변환이 켜져야 한다."""
    from cluefin_store.env import ENV_PATH_VAR
    from fastapi.testclient import TestClient

    env_file = tmp_path / ".env"
    env_file.write_text(
        "CLUEFIN_LLM_PAT=pat\nCLUEFIN_LLM_BASE_URL=https://gateway/v1\nCLUEFIN_LLM_MODEL=model-x\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(ENV_PATH_VAR, str(env_file))

    schema = TestClient(create_app(FakeRepository())).get("/api/sql/schema").json()

    assert schema["llm"]["configured"] is True
    assert schema["llm"]["model"] == "model-x"
    assert schema["llm"]["sources"]["pat"] == "CLUEFIN_LLM_PAT"


def test_llm_settings_accept_conventional_openai_variable_names(monkeypatch) -> None:
    """다른 도구가 export해 둔 이름만 있어도 켜져야 한다. 설정 없이 바로 쓰는 게 목적이다."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4-mini")

    schema = TestClient(create_app(FakeRepository())).get("/api/sql/schema").json()

    assert schema["llm"]["configured"] is True
    assert schema["llm"]["model"] == "gpt-5.4-mini"
    assert schema["llm"]["sources"] == {
        "pat": "OPENAI_API_KEY",
        "base_url": "OPENAI_BASE_URL",
        "model": "OPENAI_MODEL",
    }


def test_missing_llm_settings_message_says_where_to_put_them() -> None:
    from cluefin_web.report_jobs import missing_llm_message

    message = missing_llm_message(["CLUEFIN_LLM_PAT"])

    assert "CLUEFIN_LLM_PAT" in message
    assert ".env" in message
