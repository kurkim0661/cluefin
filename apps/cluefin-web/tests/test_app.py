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


def test_dashboard_tabs_have_targets_and_click_handler() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(FakeRepository()))

    html = client.get("/").text
    script = (PACKAGE_DIR / "static" / "app.js").read_text(encoding="utf-8")

    for tab in ("overview", "signals", "patterns", "universe", "paper"):
        assert f'data-tab="{tab}"' in html
        assert f'id="{tab}-panel"' in html
        assert f'aria-controls="{tab}-panel"' in html

    assert "setActiveTab" in script
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
