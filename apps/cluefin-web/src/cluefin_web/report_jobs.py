from __future__ import annotations

import os
import threading
from datetime import date, datetime
from typing import Any, Callable

LLM_ENV_VARS = ("CLUEFIN_LLM_PAT", "CLUEFIN_LLM_BASE_URL", "CLUEFIN_LLM_MODEL")
MAX_ERROR_CHARS = 500


def llm_settings_state() -> dict[str, Any]:
    """Report which LLM settings are present without exposing any secret value."""
    missing = [name for name in LLM_ENV_VARS if not os.getenv(name)]
    return {
        "configured": not missing,
        "missing": missing,
        "model": os.getenv("CLUEFIN_LLM_MODEL") or None,
    }


def run_daily_report(report_date: date) -> dict[str, Any]:
    from cluefin_agent.config import PatModelSettings
    from cluefin_agent.graph import build_daily_report_graph
    from cluefin_agent.repository import ResearchDataRepository
    from cluefin_store.db import ClickHouseStore

    settings = PatModelSettings.from_env()
    store = ClickHouseStore()
    store.apply_schema()
    graph = build_daily_report_graph(
        repository=ResearchDataRepository(store),
        model=settings.create_model(),
        model_name=settings.model,
    )
    result = graph.invoke({"report_date": report_date.isoformat()})
    return {
        "report_status": result["status"],
        "model": result["model"],
        "persisted": result["persisted"],
    }


class ReportGenerationJob:
    """Single-slot background runner for the LangGraph daily report."""

    def __init__(self, runner: Callable[[date], dict[str, Any]] | None = None) -> None:
        self._runner = runner or run_daily_report
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {
            "status": "idle",
            "report_date": None,
            "message": None,
            "model": None,
            "started_at": None,
            "finished_at": None,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
        state["llm"] = llm_settings_state()
        return state

    def start(self, report_date: date) -> dict[str, Any]:
        settings = llm_settings_state()
        if not settings["configured"]:
            raise ValueError("LLM 설정이 없습니다: " + ", ".join(settings["missing"]))
        with self._lock:
            if self._state["status"] == "running":
                raise RuntimeError("이미 리포트를 생성하는 중입니다.")
            self._state = {
                "status": "running",
                "report_date": report_date.isoformat(),
                "message": "근거 수집과 리포트 생성을 시작했습니다.",
                "model": settings["model"],
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": None,
            }
            thread = threading.Thread(target=self._run, args=(report_date,), daemon=True)
            self._thread = thread
        thread.start()
        return self.status()

    def _run(self, report_date: date) -> None:
        try:
            result = self._runner(report_date)
        except Exception as exc:  # surfaced to the dashboard instead of a silent failure
            self._finish(status="failed", message=_safe_error(exc))
            return
        message = "리포트를 생성했습니다."
        if result.get("report_status") == "partial":
            message = "리포트를 생성했지만 일부 필수 섹션이 비어 있어 partial로 저장했습니다."
        if not result.get("persisted", True):
            message += " (저장은 하지 않았습니다.)"
        self._finish(status="succeeded", message=message, model=result.get("model"))

    def _finish(self, *, status: str, message: str, model: str | None = None) -> None:
        with self._lock:
            self._state["status"] = status
            self._state["message"] = message
            self._state["finished_at"] = datetime.now().isoformat(timespec="seconds")
            if model:
                self._state["model"] = model


def _safe_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".strip()
    for name in LLM_ENV_VARS:
        value = os.getenv(name)
        # A PAT must never reach the browser even when a client library echoes it.
        if value and name.endswith("PAT"):
            text = text.replace(value, "***")
    return text[:MAX_ERROR_CHARS]
