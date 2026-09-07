# SQL workbench and NL→SQL agent

Two surfaces over the same core: the **SQL 워크벤치** tab in `cluefin-web` and
`cluefin-agent nl2sql`. Both read `market` only, and both are **read-only** — nothing they run
can change the database.

## Why the core lives in the store

`cluefin_store.sql_console` owns three things so the web app and the agent cannot drift apart:

- `ensure_read_only(sql)` — the guard. Also the only place that appends `LIMIT`.
- `collect_schema_context(client)` / `render_schema_prompt(context)` — the schema the human sees
  in the sidebar is literally the schema the model is prompted with.
- `run_read_only(client, sql)` / `explain_sql(client, sql)` — execution and dry-run validation.

## Read-only guard

Defence is two-layered, because a regex is not a parser:

1. **In `ensure_read_only`**: the statement must start with `SELECT / WITH / EXPLAIN /
   DESCRIBE / DESC / SHOW`; write keywords, `FORMAT`, `INTO OUTFILE`, a second statement after a
   `;`, and every filesystem/network table function (`url`, `file`, `s3`, `remote`, `mysql`, …)
   are rejected. Checks run against a **masked** copy of the SQL where string literals, backtick
   identifiers, and comments are blanked out at equal length — so `SELECT 'drop table'` passes
   while `created_at` and `formatDateTime(...)` don't trip the `create`/`format` rules.
2. **At the server**: every query goes out with `readonly=2` plus `max_execution_time`,
   `max_result_rows`, `max_result_bytes`, `result_overflow_mode=break`. ClickHouse itself refuses
   an `INSERT`/`CREATE` even if the regex layer were bypassed. (`readonly=1` would also forbid
   sending those settings, hence `2`.)

Rows are capped at 2000 (`MAX_ROWS`); the runner asks for one extra row to report `truncated`.

## Schema context

`collect_schema_context` reads `system.tables` / `system.columns` and tags every column with a
role — `time`, `dimension`, `fact`, or `meta` (`run_id`, `*_json`, `*_at`, hidden by default).
Two extras exist because they decide whether a Korean question can be answered at all:

- **`samples`**: distinct values of `LowCardinality` dimensions (≤30 values, ≤8 columns per
  table, skipped for tables over 5M rows) — so the model filters on `'수도권'`, not a guess.
- **`catalog`**: for small code tables (≤200 rows) with a `String` `*_id` and a `name_ko`, the
  full code→name mapping. This is what turns "미분양" into `metric_id = 'unsold_housing'`.
  Deliberately excludes UUID-keyed document tables (reports, news) — their titles are noise.

Rendered prompt size for the whole `market` schema is ~16k chars (~5k tokens). The web app caches
the context for 5 minutes (`SCHEMA_CACHE_SECONDS`).

## Agent loop

`cluefin_agent.sql_agent` is a LangGraph graph: `load_schema → generate_sql → validate_sql`, with
a conditional edge back to `generate_sql`. Validation is `ensure_read_only` followed by `EXPLAIN`
against ClickHouse, which catches unknown tables and columns without running the query. On
failure the server's own error plus the failed SQL are fed back into the next attempt (3 max).
The model must answer with one JSON object (`sql`, `explanation`, `notes`); fenced or chatty
replies are salvaged.

**ClickHouse gotcha the prompt pins down**: an unquoted non-ASCII identifier is a syntax error,
so Korean aliases must be backticked (`AS \`기간\``). The web skeleton builder does the same via
`sqlIdentifier()`. Without this rule every generated query burns its retries.

## Web surface

| Route | Purpose |
|---|---|
| `GET /api/sql/schema` | tables, column roles, sample values, catalogs, `llm` state |
| `POST /api/sql/run` | `{sql, max_rows?}` → `{columns, rows, row_count, truncated, elapsed_ms}` |
| `POST /api/sql/translate` | `{question}` → agent result (400 on bad input, 502 on model failure) |
| `GET/POST /api/sql/saved`, `DELETE /api/sql/saved/{name}` | named queries |

The tab has three parts: a schema panel (pick dimension/fact/time columns, click a sample value to
insert `column = 'value'` at the cursor, "골격 쿼리 만들기" writes the `GROUP BY` skeleton), the
editor (natural-language box, SQL textarea, Ctrl+Enter to run, save/CSV), and the result grid.
When `CLUEFIN_LLM_*` is unset the natural-language box is disabled and the rest still works.

Saved queries live in `market.saved_sql_queries`, `ReplacingMergeTree(updated_at) ORDER BY name` —
saving the same name overwrites and keeps the original `created_at`. Deletion is an `ALTER … DELETE`
mutation issued by the repository, not through the read-only path.

## CLI

```bash
uv run cluefin-agent nl2sql "수도권 미분양과 서울 아파트 매매지수를 월별로 비교"
uv run cluefin-agent nl2sql "최근 6개월 지가변동률 상위 지역" --run --rows 10
uv run cluefin-agent nl2sql "전세가율 추이" --json
```

Needs `CLUEFIN_LLM_PAT`, `CLUEFIN_LLM_BASE_URL`, `CLUEFIN_LLM_MODEL` in `.env` or the environment
(same gateway as the daily report agent). `--run` exits 1 when validation failed, so it is safe in a pipeline.
