# Daily research agent

`cluefin-agent` is a LangGraph workflow that reads the same ClickHouse evidence used by the dashboard and produces one Korean daily research report.

```text
collect_evidence
  -> prepare_prompt
  -> generate_report (LangChain ChatOpenAI-compatible model)
  -> validate_report
  -> persist_report (ClickHouse)
```

The evidence snapshot includes macro/liquidity indicators, Korean and US universes, DART aggregate fundamentals, crypto/on-chain/derivatives metrics, capital-area real-estate series, validated pattern performance, recent signals, and the forward-looking sections of the last few stored reports. The prompt prohibits invented values and requires observation, impact, interpretation, counter-evidence, and data limitations.

## Report sections

`validate_report` marks a run `partial` when any of these headings is missing.

| Section | Contract |
|---|---|
| 한눈에 보는 결론 | One-sentence summary, a 5-column table, three "지금 가장 중요한 것" bullets. |
| 지난 리포트 점검 | Scores 2–3 claims from `previous_reports` as `이어짐 / 반전 / 미확인` against today's data. |
| 글로벌 · 한국 · 코인 | Observation blocks: 관측 / 영향 / 해석 / 반대 근거. |
| 부동산과 주거 시장 | Sale/jeonse index direction, capital-area vs national gap, supply signals. |
| 패턴 검증과 오늘의 시그널 | Backtested pattern performance and recent detections. |
| 자산별 포지션 가이드 | `| 자산 \| 스탠스 \| 근거 \| 무효화 신호 |` for 한국 주식 · 미국 주식 · 금리·채권 · 코인 · 부동산. Stance is one of `비중 확대 / 유지 / 축소 / 관망`. |
| 오늘 확인할 체크리스트 · 데이터 한계와 반대 근거 | Watch items and what the snapshot cannot answer. |

The stance column is a data-derived scenario with an invalidation trigger, not a buy or sell instruction, and it never names an individual security.

### Prior-report continuity

`previous_reports` carries the three most recent report dates before the requested one (newest run per day). Only sections whose heading mentions 한눈에 · 포지션 · 체크리스트 · 점검 · 우선순위 · 반대 근거 are included, each truncated to 900 characters, so the prompt keeps yesterday's expectations without replaying whole reports.

### Real-estate evidence

`real_estate` groups `market.real_estate_observations` by metric × region × property type × deal type over an 800-day window, limited to 전국/수도권/서울/경기/인천 and 종합/아파트/전체. Each entry carries `value_display`, `change_pct_display` (previous period) and `yoy_pct_display` (13th observation back, i.e. year-ago for monthly series), so a one-month bounce reads differently from a trend reversal.

## PAT configuration

The PAT is used as an `Authorization: Bearer` credential and is never printed or stored in ClickHouse.

Put the three values in the repo-root `.env` (`cp .env.sample .env`); `cluefin-store`, `cluefin-agent`,
and `cluefin-web` load that file at startup and search upward from the working directory, so running
from a subpackage still finds it. Exported shell variables always win over the file, and
`CLUEFIN_ENV_FILE` points at a different path.

Two conveniences exist so that a working setup needs no copying of secrets:

- **`${VAR}` expansion** — `CLUEFIN_LLM_PAT=${MY_GATEWAY_TOKEN}` reads whatever another tool already
  exported, so the credential is never duplicated into a second file.
- **Conventional fallbacks** — when a `CLUEFIN_LLM_*` variable is unset the resolver tries
  `LLM_API_KEY` / `OPENAI_API_KEY`, then `LLM_BASE_URL` / `OPENAI_BASE_URL` / `OPENAI_API_BASE`,
  then `LLM_MODEL` / `OPENAI_MODEL` (`cluefin_agent.config.SETTING_ENV_VARS`). `/api/sql/schema`
  reports which variable each value came from under `llm.sources`, so a surprising model name is
  traceable.

Gateway failures are translated once, in `cluefin_agent.gateway.gateway_hint`, so a spending-limit
429, a 401, and a connection error read as three different instructions instead of a traceback.
The original message is kept in parentheses.

```bash
# .env
CLUEFIN_LLM_PAT=...
CLUEFIN_LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
CLUEFIN_LLM_MODEL=your-model-name
```

Equivalently, for a one-off shell:

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

The same graph runs from the web UI. Start Cluefin Web with the three `CLUEFIN_LLM_*` variables in `.env` or in its process environment and use the **리포트 생성** button on the market-context tab. Without them the button is disabled and the panel names the missing variables instead of failing on submit.

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
