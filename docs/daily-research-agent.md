# Daily research agent

`cluefin-agent` is a LangGraph workflow that reads the same ClickHouse evidence used by the dashboard and produces one Korean daily research report.

```text
collect_evidence
  -> prepare_prompt
  -> generate_report (LangChain ChatOpenAI-compatible model)
  -> validate_report
  -> persist_report (ClickHouse)
```

The evidence snapshot includes macro/liquidity indicators, Korean and US universes, DART aggregate fundamentals, crypto/on-chain/derivatives metrics, validated pattern performance, and recent signals. The prompt prohibits invented values and requires observation, impact, interpretation, counter-evidence, and data limitations.

## PAT configuration

The PAT is used as an `Authorization: Bearer` credential and is never printed or stored in ClickHouse.

```bash
export CLUEFIN_LLM_PAT='...'
export CLUEFIN_LLM_BASE_URL='https://your-openai-compatible-endpoint/v1'
export CLUEFIN_LLM_MODEL='your-model-name'
```

Check the evidence without calling the LLM:

```bash
uv run cluefin-agent daily-report --date 2026-08-26 --dry-run
```

Generate and persist the report:

```bash
uv run cluefin-agent daily-report --date 2026-08-26
```

## Generating from the dashboard

The same graph runs from the web UI. Start Cluefin Web with the three `CLUEFIN_LLM_*` variables in its process environment and use the **리포트 생성** button on the market-context tab. Without them the button is disabled and the panel names the missing variables instead of failing on submit.

| Route | Purpose |
|---|---|
| `POST /api/research-reports/generate` | Start one background run; optional `{"report_date": "YYYY-MM-DD"}`. Returns 409 while another run is in flight. |
| `GET /api/research-reports/status` | Poll job state and LLM configuration. Never returns the PAT. |
| `GET /api/research-reports` | Stored reports, newest first, without markdown. |
| `GET /api/research-reports/{report_id}` | One stored report including markdown. |
| `GET /api/research-report` | Latest stored report. |

`market.daily_research_reports` is ordered by `(report_date, report_id)`, so regenerating the same day keeps the earlier run instead of replacing it. Every run stays selectable in the dashboard history list.

## Daily schedule

Run indicator collection before report generation. For cron or launchd, keep secrets in the process environment rather than command arguments.

```bash
uv run cluefin-store update-indicators --start-date 2026-08-12 --end-date 2026-08-26
uv run cluefin-agent daily-report --date 2026-08-26
```

Use a moving 14-day collection window in automation; monthly and revised sources are idempotently replaced by `collected_at`.

## Contracted metrics

Metrics that require labeled wallets, analyst consensus, ETF flow aggregation, or unlock calendars can be supplied through either CSV import or a PAT-authenticated normalized endpoint.

```bash
export CLUEFIN_VENDOR_METRICS_URL='https://vendor.example/v1/metrics'
export CLUEFIN_VENDOR_PAT='...'
uv run cluefin-store update-indicators --provider licensed --start-date 2026-08-01 --end-date 2026-08-26
```

The endpoint response contract is:

```json
{
  "data": [
    {
      "indicator_id": "btc_spot_etf_flow",
      "period": "2026-08-26",
      "value": 125000000,
      "vendor": "provider-name",
      "metadata": {"currency": "USD"}
    }
  ]
}
```

The PAT remains in the HTTP header and is not included in observation metadata.
