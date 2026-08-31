from cluefin_web.app import PACKAGE_DIR, create_app


class FakeRepository:
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
    # 차트 컨테이너와 차트 형태 셀렉트가 같은 id를 쓰면 캔버스 크기가 0이 된다.
    assert html.count('id="estate-chart"') == 1
