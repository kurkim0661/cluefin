from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from cluefin_store.sql_console import (
    SqlSafetyError,
    collect_schema_context,
    column_role,
    ensure_read_only,
    explain_sql,
    mask_literals,
    render_schema_prompt,
    run_read_only,
)


class FakeResult:
    def __init__(self, rows: list[tuple], columns: list[str], types: list[str] | None = None) -> None:
        self.result_rows = rows
        self.column_names = columns
        self.column_types = types or []


class FakeClient:
    """SQL 조각으로 응답을 고르는 가짜 ClickHouse 클라이언트."""

    def __init__(self, responses: list[tuple[str, FakeResult]], fail_on: str | None = None) -> None:
        self.responses = responses
        self.fail_on = fail_on
        self.calls: list[tuple[str, dict | None, dict | None]] = []

    def query(self, sql: str, parameters: dict | None = None, settings: dict | None = None) -> FakeResult:
        self.calls.append((sql, parameters, settings))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError(f"Code: 47. DB::Exception: Unknown expression identifier `{self.fail_on}`. (stack)")
        for marker, result in self.responses:
            if marker in sql:
                return result
        return FakeResult([], [])


def test_mask_literals_keeps_length_and_hides_strings_and_comments() -> None:
    sql = "SELECT 'drop table' /* delete */ -- insert\nFROM market.t"

    masked = mask_literals(sql)

    assert len(masked) == len(sql)
    for hidden in ("drop", "delete", "insert"):
        assert hidden not in masked
    assert "FROM market.t" in masked


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO market.real_estate_observations VALUES (1)",
        "ALTER TABLE market.real_estate_observations DELETE WHERE 1",
        "DROP TABLE market.real_estate_observations",
        "SELECT 1; SELECT 2",
        "SELECT * FROM url('http://x', CSV)",
        "SELECT * FROM file('/etc/passwd')",
        "SELECT * FROM market.t INTO OUTFILE '/tmp/x'",
        "SELECT 1 FORMAT JSON",
        "",
        "   ",
    ],
)
def test_ensure_read_only_rejects_writes_and_escapes(sql: str) -> None:
    with pytest.raises(SqlSafetyError):
        ensure_read_only(sql)


def test_ensure_read_only_allows_reads_and_appends_limit() -> None:
    assert ensure_read_only("SELECT 1", max_rows=100).endswith("LIMIT 100")
    assert ensure_read_only("SELECT 1 LIMIT 3", max_rows=100) == "SELECT 1 LIMIT 3"
    # 세미콜론 하나로 끝나는 문장은 사람이 흔히 붙이므로 지우고 받아 준다.
    assert ensure_read_only("SELECT 1 LIMIT 3;") == "SELECT 1 LIMIT 3"
    with_cte = ensure_read_only("WITH t AS (SELECT 1 AS a) SELECT a FROM t", max_rows=50)
    assert with_cte.endswith("LIMIT 50")
    for statement in ("SHOW TABLES FROM market", "DESCRIBE market.real_estate_observations"):
        assert ensure_read_only(statement) == statement


def test_ensure_read_only_keeps_settings_clause_after_limit() -> None:
    prepared = ensure_read_only("SELECT 1 SETTINGS max_threads = 2", max_rows=10)

    assert prepared.index("LIMIT 10") < prepared.index("SETTINGS")


def test_ensure_read_only_does_not_trip_on_lookalike_identifiers() -> None:
    sql = "SELECT created_at, is_deleted, formatDateTime(period, '%F') AS d FROM market.t LIMIT 1"

    assert ensure_read_only(sql) == sql


def test_run_read_only_marks_truncation_and_serialises_values() -> None:
    rows = [
        (date(2026, 6, 1), Decimal("1.5"), UUID("00000000-0000-0000-0000-000000000001")),
        (date(2026, 7, 1), Decimal("2.5"), UUID("00000000-0000-0000-0000-000000000002")),
        (date(2026, 8, 1), Decimal("3.5"), UUID("00000000-0000-0000-0000-000000000003")),
    ]
    client = FakeClient([("SELECT", FakeResult(rows, ["period", "value", "run_id"], ["Date", "Decimal", "UUID"]))])

    result = run_read_only(client, "SELECT period, value, run_id FROM market.t", max_rows=2)

    assert result["rows"] == [
        ["2026-06-01", 1.5, "00000000-0000-0000-0000-000000000001"],
        ["2026-07-01", 2.5, "00000000-0000-0000-0000-000000000002"],
    ]
    assert result["truncated"] is True
    assert result["row_count"] == 2
    assert result["columns"] == ["period", "value", "run_id"]
    settings = client.calls[0][2]
    assert settings["readonly"] == 2
    assert settings["max_result_rows"] == 3


def test_run_read_only_refuses_before_touching_the_client() -> None:
    client = FakeClient([])

    with pytest.raises(SqlSafetyError):
        run_read_only(client, "DROP TABLE market.t")

    assert client.calls == []


def test_explain_sql_returns_server_message_for_unknown_column() -> None:
    client = FakeClient([], fail_on="nope")

    assert explain_sql(client, "SELECT nope FROM market.t") == "Unknown expression identifier `nope`. (stack)"
    assert explain_sql(client, "SELECT metric_id FROM market.t") is None
    assert explain_sql(client, "DELETE FROM market.t") is not None


@pytest.mark.parametrize(
    ("name", "type_name", "expected"),
    [
        ("period", "Date", "time"),
        ("collected_at", "DateTime64(3, 'Asia/Seoul')", "meta"),
        ("metadata_json", "String", "meta"),
        ("run_id", "UUID", "meta"),
        ("metric_id", "LowCardinality(String)", "dimension"),
        ("region", "LowCardinality(String)", "dimension"),
        ("value", "Float64", "fact"),
        ("volume", "UInt64", "fact"),
        ("close", "Decimal(18, 4)", "fact"),
        ("importance", "UInt8", "fact"),
        ("is_active", "UInt8", "dimension"),
        # run_id는 적재 흔적이라 감추지만 report_id는 사람이 실제로 걸러 보는 값이다.
        ("report_id", "UUID", "dimension"),
    ],
)
def test_column_role_splits_dimensions_from_facts(name: str, type_name: str, expected: str) -> None:
    assert column_role(name, type_name) == expected


def _schema_client() -> FakeClient:
    return FakeClient(
        [
            (
                "FROM system.tables",
                FakeResult(
                    [("real_estate_observations", "ReplacingMergeTree", "metric_id, region, period", 14640)],
                    ["name", "engine", "sorting_key", "total_rows"],
                ),
            ),
            (
                "FROM system.columns",
                FakeResult(
                    [
                        ("real_estate_observations", "period", "Date", ""),
                        ("real_estate_observations", "metric_id", "LowCardinality(String)", "지표"),
                        ("real_estate_observations", "region", "LowCardinality(String)", ""),
                        ("real_estate_observations", "value", "Float64", ""),
                        ("real_estate_observations", "run_id", "UUID", ""),
                    ],
                    ["table", "name", "type", "comment"],
                ),
            ),
            (
                "groupUniqArray",
                FakeResult(
                    [(["unsold_housing", "house_sale_price_index"], ["서울", "수도권"], "2016-09-01", "2026-06-01")],
                    ["metric_id", "region", "__first", "__last"],
                ),
            ),
        ]
    )


def test_collect_schema_context_classifies_columns_and_samples_dimensions() -> None:
    context = collect_schema_context(_schema_client(), database="market")

    table = context["tables"][0]
    assert table["table"] == "real_estate_observations"
    assert table["total_rows"] == 14640
    roles = {column["name"]: column["role"] for column in table["columns"]}
    assert roles == {
        "period": "time",
        "metric_id": "dimension",
        "region": "dimension",
        "value": "fact",
        "run_id": "meta",
    }
    assert table["samples"]["metric_id"] == ["house_sale_price_index", "unsold_housing"]
    assert table["period"] == {"column": "period", "first": "2016-09-01", "last": "2026-06-01"}


def test_render_schema_prompt_lists_values_and_facts_without_plumbing() -> None:
    prompt = render_schema_prompt(collect_schema_context(_schema_client()))

    assert "market.real_estate_observations" in prompt
    assert "rows=14,640" in prompt
    assert "period 2016-09-01~2026-06-01" in prompt
    assert "값: house_sale_price_index, unsold_housing" in prompt
    assert "집계 대상(fact): value" in prompt
    assert "run_id" not in prompt


def _catalog_client(id_column: str, id_type: str, label_column: str) -> FakeClient:
    return FakeClient(
        [
            (
                "FROM system.tables",
                FakeResult(
                    [("catalog", "ReplacingMergeTree", id_column, 3)], ["name", "engine", "sorting_key", "total_rows"]
                ),
            ),
            (
                "FROM system.columns",
                FakeResult(
                    [("catalog", id_column, id_type, ""), ("catalog", label_column, "String", "")],
                    ["table", "name", "type", "comment"],
                ),
            ),
            ("AS key", FakeResult([("unsold_housing", "미분양 주택")], ["key", "label"])),
        ]
    )


def test_render_schema_prompt_maps_korean_names_for_small_code_tables() -> None:
    prompt = render_schema_prompt(collect_schema_context(_catalog_client("metric_id", "String", "name_ko")))

    assert "코드→이름: unsold_housing=미분양 주택" in prompt


def test_catalog_skips_uuid_keyed_document_tables() -> None:
    context = collect_schema_context(_catalog_client("report_id", "UUID", "title"))

    assert context["tables"][0]["catalog"] == []


def test_collect_schema_context_skips_sampling_huge_tables() -> None:
    client = FakeClient(
        [
            (
                "FROM system.tables",
                FakeResult(
                    [("daily_ohlcv", "ReplacingMergeTree", "symbol", 90_000_000)],
                    ["name", "engine", "sorting_key", "total_rows"],
                ),
            ),
            (
                "FROM system.columns",
                FakeResult([("daily_ohlcv", "symbol", "String", "")], ["table", "name", "type", "comment"]),
            ),
        ]
    )

    context = collect_schema_context(client)

    assert context["tables"][0]["sampled"] is False
    assert not any("groupUniqArray" in call[0] for call in client.calls)
