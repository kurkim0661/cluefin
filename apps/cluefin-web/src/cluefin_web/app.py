from __future__ import annotations

from datetime import date
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from cluefin_web.report_jobs import ReportGenerationJob
from cluefin_web.repository import DashboardRepository

PACKAGE_DIR = Path(__file__).parent


def create_app(
    repository: DashboardRepository | None = None,
    report_job: ReportGenerationJob | None = None,
) -> FastAPI:
    app = FastAPI(title="Cluefin Web")
    repo = repository or DashboardRepository.from_env()
    app.state.repository = repo
    app.state.report_job = report_job or ReportGenerationJob()
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (PACKAGE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    @app.get("/api/dashboard")
    def dashboard() -> dict:
        return app.state.repository.snapshot()

    @app.get("/api/market-pulse")
    def market_pulse() -> dict:
        return app.state.repository.market_pulse()

    @app.get("/api/research-report")
    def research_report() -> dict | None:
        return app.state.repository.latest_research_report()

    @app.get("/api/research-reports")
    def research_reports(limit: int = 30) -> list[dict]:
        return app.state.repository.research_report_history(limit)

    @app.get("/api/research-reports/status")
    def research_report_status() -> dict:
        return app.state.report_job.status()

    @app.post("/api/research-reports/generate")
    def generate_research_report(payload: dict | None = None) -> dict:
        raw_date = (payload or {}).get("report_date")
        try:
            report_date = date.fromisoformat(raw_date) if raw_date else date.today()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="report_date는 YYYY-MM-DD 형식이어야 합니다.") from exc
        try:
            return app.state.report_job.start(report_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/research-reports/{report_id}")
    def research_report_detail(report_id: str) -> dict:
        report = app.state.repository.research_report(report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="해당 리포트를 찾을 수 없습니다.")
        return report

    @app.get("/api/real-estate/meta")
    def real_estate_meta() -> dict:
        return app.state.repository.real_estate_meta()

    @app.post("/api/real-estate/query")
    def real_estate_query(payload: dict) -> dict:
        try:
            return app.state.repository.real_estate_query(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/pattern-performance")
    def pattern_performance() -> list[dict]:
        return app.state.repository.pattern_performance()

    @app.get("/api/signals")
    def signals() -> list[dict]:
        return app.state.repository.latest_signals()

    @app.get("/api/universe")
    def universe() -> list[dict]:
        return app.state.repository.universe_members()

    @app.get("/api/technicals")
    def technicals() -> list[dict]:
        return app.state.repository.latest_technicals()

    @app.get("/api/sentiment")
    def sentiment() -> list[dict]:
        return app.state.repository.latest_sentiment()

    @app.get("/api/symbols/{symbol}/chart")
    def symbol_chart(symbol: str) -> dict:
        return app.state.repository.symbol_chart(symbol)

    @app.get("/api/paper")
    def paper_dashboard() -> dict:
        return app.state.repository.paper_dashboard()

    @app.post("/api/paper/accounts")
    def create_paper_account(payload: dict) -> dict:
        return app.state.repository.create_paper_account(payload)

    @app.post("/api/paper/strategies")
    def create_paper_strategy(payload: dict) -> dict:
        return app.state.repository.create_paper_strategy(payload)

    @app.post("/api/paper/backtests")
    def run_paper_backtest(payload: dict) -> dict:
        try:
            return app.state.repository.run_paper_backtest(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)
