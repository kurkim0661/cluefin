"""읽기 전용 SQL 콘솔과 스키마 컨텍스트.

웹 워크벤치와 NL→SQL 에이전트가 같은 가드·같은 스키마 설명을 쓰도록 store에 둔다.
방어는 두 겹이다. 여기서 문법을 걸러내고, ClickHouse에는 `readonly=2`로 보내 서버가 한 번 더 막는다.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

SCHEMA_DATABASE = "market"
MAX_ROWS = 2000
MAX_SQL_CHARS = 20000
MAX_EXECUTION_SECONDS = 20
SAMPLE_LIMIT = 30
SAMPLE_COLUMN_LIMIT = 8
# 사람은 "미분양"이라고 묻고 테이블은 unsold_housing으로 저장한다. 작은 카탈로그 테이블은 코드→이름을 다 실어 준다.
CATALOG_ROW_LIMIT = 200
# groupUniqArray로 값 목록을 뽑을 때 큰 테이블은 건너뛴다. 스키마 화면 한 번에 전체 스캔을 걸지 않기 위한 상한.
SAMPLE_TABLE_ROW_LIMIT = 5_000_000

READ_ONLY_SETTINGS: dict[str, Any] = {
    # 1은 설정 변경까지 막아 max_execution_time을 함께 보낼 수 없다. 2는 읽기 전용 + 설정 허용.
    "readonly": 2,
    "max_execution_time": MAX_EXECUTION_SECONDS,
    "max_result_bytes": 32 * 1024 * 1024,
    "result_overflow_mode": "break",
}

ALLOWED_LEADING_KEYWORDS = ("select", "with", "explain", "describe", "desc", "show")
FORBIDDEN_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "alter",
    "create",
    "drop",
    "truncate",
    "attach",
    "detach",
    "rename",
    "grant",
    "revoke",
    "optimize",
    "kill",
    "use",
    "set",
    "format",
    "outfile",
)
# 파일·네트워크·다른 DB로 나가는 테이블 함수는 읽기 전용이어도 차단한다.
FORBIDDEN_TABLE_FUNCTIONS = (
    "url",
    "urlCluster",
    "file",
    "s3",
    "s3Cluster",
    "remote",
    "remoteSecure",
    "cluster",
    "clusterAllReplicas",
    "mysql",
    "postgresql",
    "mongodb",
    "jdbc",
    "odbc",
    "hdfs",
    "azureBlobStorage",
    "input",
    "executable",
    "sqlite",
    "redis",
)

TIME_TYPES = ("Date", "DateTime")
NUMERIC_TYPES = ("Int", "UInt", "Float", "Decimal")


class SqlSafetyError(ValueError):
    """읽기 전용 규칙을 어긴 SQL."""


def mask_literals(sql: str) -> str:
    """문자열 리터럴과 주석을 같은 길이의 공백으로 덮는다.

    길이를 유지하므로 마스킹한 문자열에서 찾은 위치를 원본에 그대로 쓸 수 있다.
    """
    masked = list(sql)
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if char in "'\"`":
            masked[index] = " "
            index += 1
            while index < length:
                if sql[index] == "\\" and index + 1 < length:
                    masked[index] = masked[index + 1] = " "
                    index += 2
                    continue
                masked[index] = " "
                index += 1
                if sql[index - 1] == char:
                    break
            continue
        if sql.startswith("--", index):
            while index < length and sql[index] != "\n":
                masked[index] = " "
                index += 1
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            end = length if end == -1 else end + 2
            for position in range(index, end):
                masked[position] = " "
            index = end
            continue
        index += 1
    return "".join(masked)


def ensure_read_only(sql: str, *, max_rows: int = MAX_ROWS) -> str:
    """SQL이 읽기 전용인지 확인하고, LIMIT이 없으면 붙여서 돌려준다."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise SqlSafetyError("SQL이 비어 있습니다.")
    if len(text) > MAX_SQL_CHARS:
        raise SqlSafetyError(f"SQL이 너무 깁니다. {MAX_SQL_CHARS}자 이내로 줄여 주세요.")

    masked = mask_literals(text)
    if ";" in masked:
        raise SqlSafetyError("한 번에 한 문장만 실행할 수 있습니다. 세미콜론으로 나눈 문장을 지워 주세요.")

    leading = re.match(r"\s*([a-zA-Z]+)", masked)
    keyword = (leading.group(1) if leading else "").lower()
    if keyword not in ALLOWED_LEADING_KEYWORDS:
        raise SqlSafetyError(
            f"읽기 전용 콘솔이라 {', '.join(word.upper() for word in ALLOWED_LEADING_KEYWORDS)}로 시작하는 "
            "문장만 실행할 수 있습니다."
        )
    for word in FORBIDDEN_KEYWORDS:
        if word == keyword:
            continue
        if re.search(rf"\b{word}\b", masked, flags=re.IGNORECASE):
            raise SqlSafetyError(f"`{word.upper()}`는 읽기 전용 콘솔에서 쓸 수 없습니다.")
    for function in FORBIDDEN_TABLE_FUNCTIONS:
        if re.search(rf"\b{function}\s*\(", masked, flags=re.IGNORECASE):
            raise SqlSafetyError(f"`{function}()` 테이블 함수는 외부 자원에 접근하므로 쓸 수 없습니다.")

    if keyword not in {"select", "with"}:
        return text
    if re.search(r"\blimit\b", masked, flags=re.IGNORECASE):
        return text
    settings_match = None
    for match in re.finditer(r"\bsettings\b", masked, flags=re.IGNORECASE):
        settings_match = match
    if settings_match:
        head, tail = text[: settings_match.start()].rstrip(), text[settings_match.start() :]
        return f"{head}\nLIMIT {max_rows}\n{tail}"
    return f"{text}\nLIMIT {max_rows}"


def run_read_only(client: Any, sql: str, *, max_rows: int = MAX_ROWS) -> dict[str, Any]:
    """가드를 통과한 SQL을 실행하고 표로 쓸 수 있는 결과를 돌려준다."""
    prepared = ensure_read_only(sql, max_rows=max_rows)
    settings = dict(READ_ONLY_SETTINGS)
    # 한 행 더 받아 보고 잘렸는지 판단한다.
    settings["max_result_rows"] = max_rows + 1
    started = time.perf_counter()
    result = client.query(prepared, settings=settings)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    rows = [[_json_value(value) for value in row] for row in result.result_rows]
    truncated = len(rows) > max_rows
    return {
        "sql": prepared,
        "columns": list(getattr(result, "column_names", []) or []),
        "column_types": [str(item) for item in (getattr(result, "column_types", None) or [])],
        "rows": rows[:max_rows],
        "row_count": min(len(rows), max_rows),
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
    }


def explain_sql(client: Any, sql: str) -> str | None:
    """EXPLAIN으로 테이블·컬럼 이름까지 검증한다. 통과하면 None, 아니면 오류 메시지."""
    try:
        prepared = ensure_read_only(sql)
    except SqlSafetyError as exc:
        return str(exc)
    if not re.match(r"\s*(select|with)\b", prepared, flags=re.IGNORECASE):
        return None
    try:
        client.query(f"EXPLAIN {prepared}", settings=READ_ONLY_SETTINGS)
    except Exception as exc:  # 서버 메시지를 그대로 에이전트에 되먹인다.
        return _short_error(exc)
    return None


def probe_rows(client: Any, sql: str) -> int | None:
    """검증된 SQL을 한 행만 받아 실제로 결과가 있는지 본다.

    EXPLAIN은 문법과 이름만 본다. 지표마다 유효한 차원 조합이 달라서, 문법은 맞고 결과는 0행인
    쿼리가 흔하다. 알 수 없으면(실행 실패) None을 돌려 재시도 판단에서 제외한다.
    """
    try:
        return run_read_only(client, sql, max_rows=1)["row_count"]
    except Exception:
        return None


def column_role(name: str, type_name: str) -> str:
    """컬럼을 시간축·차원·팩트·적재 메타로 나눈다."""
    lowered = name.lower()
    if lowered == "run_id" or lowered.endswith("_json") or lowered.endswith("_at"):
        return "meta"
    base = type_name.replace("LowCardinality(", "").replace("Nullable(", "")
    if base.startswith(TIME_TYPES):
        return "time"
    if base.startswith(NUMERIC_TYPES):
        if lowered.endswith("_id") or lowered.startswith("is_"):
            return "dimension"
        return "fact"
    return "dimension"


def collect_schema_context(
    client: Any,
    *,
    database: str = SCHEMA_DATABASE,
    with_samples: bool = True,
) -> dict[str, Any]:
    """`system` 테이블에서 스키마를 읽고 차원 컬럼의 실제 값 목록까지 붙인다."""
    tables = _rows(
        client,
        """
        SELECT name, engine, sorting_key, total_rows
        FROM system.tables
        WHERE database = %(database)s AND engine NOT LIKE '%%View'
        ORDER BY name
        """,
        {"database": database},
    )
    columns = _rows(
        client,
        """
        SELECT table, name, type, comment
        FROM system.columns
        WHERE database = %(database)s
        ORDER BY table, position
        """,
        {"database": database},
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in columns:
        grouped.setdefault(str(row["table"]), []).append(
            {
                "name": str(row["name"]),
                "type": str(row["type"]),
                "role": column_role(str(row["name"]), str(row["type"])),
                "comment": str(row.get("comment") or ""),
            }
        )

    result: list[dict[str, Any]] = []
    for table in tables:
        name = str(table["name"])
        table_columns = grouped.get(name, [])
        if not table_columns:
            continue
        entry: dict[str, Any] = {
            "database": database,
            "table": name,
            "engine": str(table.get("engine") or ""),
            "sorting_key": str(table.get("sorting_key") or ""),
            "total_rows": int(table.get("total_rows") or 0),
            "columns": table_columns,
        }
        if with_samples:
            entry.update(_table_profile(client, database, name, table_columns, entry["total_rows"]))
        result.append(entry)
    return {"database": database, "tables": result}


def _table_profile(
    client: Any,
    database: str,
    table: str,
    columns: list[dict[str, Any]],
    total_rows: int,
) -> dict[str, Any]:
    """LowCardinality 차원의 값 목록과 시간축 범위를 한 번의 스캔으로 모은다."""
    if total_rows > SAMPLE_TABLE_ROW_LIMIT:
        return {"samples": {}, "period": None, "catalog": [], "sampled": False}
    catalog = _table_catalog(client, database, table, columns, total_rows)
    sampled = [column for column in columns if column["role"] == "dimension" and "LowCardinality" in column["type"]][
        :SAMPLE_COLUMN_LIMIT
    ]
    time_column = next((column["name"] for column in columns if column["role"] == "time"), None)
    if not sampled and not time_column:
        return {"samples": {}, "period": None, "catalog": catalog, "sampled": bool(catalog)}
    selects = [f"groupUniqArray({SAMPLE_LIMIT + 1})(`{column['name']}`) AS `{column['name']}`" for column in sampled]
    if time_column:
        selects.append(f"toString(min(`{time_column}`)) AS __first")
        selects.append(f"toString(max(`{time_column}`)) AS __last")
    rows = _rows(client, f"SELECT {', '.join(selects)} FROM `{database}`.`{table}`")  # 식별자는 시스템 테이블 출처
    if not rows:
        return {"samples": {}, "period": None, "catalog": catalog, "sampled": bool(catalog)}
    row = rows[0]
    samples: dict[str, list[str]] = {}
    for column in sampled:
        values = row.get(column["name"]) or []
        if len(values) > SAMPLE_LIMIT:
            continue  # 값이 너무 많으면 목록이 도움이 안 되므로 생략한다.
        samples[column["name"]] = sorted(str(value) for value in values)
    period = None
    if time_column and row.get("__first"):
        period = {"column": time_column, "first": str(row["__first"]), "last": str(row["__last"])}
    return {"samples": samples, "period": period, "catalog": catalog, "sampled": True}


def _table_catalog(
    client: Any,
    database: str,
    table: str,
    columns: list[dict[str, Any]],
    total_rows: int,
) -> list[dict[str, str]]:
    """코드 컬럼과 한글 이름 컬럼을 함께 가진 작은 표는 전체 대응 목록을 싣는다."""
    if not 0 < total_rows <= CATALOG_ROW_LIMIT:
        return []
    names = [column["name"] for column in columns]
    # UUID 키와 title은 리포트·뉴스 같은 본문 테이블이라 대응 목록이 의미 없다. 큐레이션된 코드 표만 싣는다.
    key = next(
        (column["name"] for column in columns if column["name"].endswith("_id") and "String" in column["type"]),
        None,
    )
    label = "name_ko" if "name_ko" in names else None
    if not key or not label:
        return []
    rows = _rows(
        client,
        f"SELECT `{key}` AS key, `{label}` AS label FROM `{database}`.`{table}` LIMIT {CATALOG_ROW_LIMIT}",
    )
    return [{"key": str(row["key"]), "label": str(row["label"])} for row in rows if row.get("key")]


def render_schema_prompt(context: dict[str, Any], *, focus_tables: tuple[str, ...] = ()) -> str:
    """LLM 프롬프트에 넣을 스키마 설명. 토큰을 아끼려고 한 컬럼 한 줄로 압축한다."""
    database = context.get("database", SCHEMA_DATABASE)
    tables = list(context.get("tables") or [])
    if focus_tables:
        order = {name: index for index, name in enumerate(focus_tables)}
        tables.sort(key=lambda item: (order.get(item["table"], len(order)), item["table"]))
    lines: list[str] = []
    for table in tables:
        header = f"{database}.{table['table']}"
        facts = [column["name"] for column in table["columns"] if column["role"] == "fact"]
        details = [f"engine={table['engine']}"]
        if table.get("total_rows"):
            details.append(f"rows={table['total_rows']:,}")
        if table.get("sorting_key"):
            details.append(f"ORDER BY ({table['sorting_key']})")
        period = table.get("period")
        if period:
            details.append(f"{period['column']} {period['first']}~{period['last']}")
        lines.append(f"- {header} ({', '.join(details)})")
        samples = table.get("samples") or {}
        for column in table["columns"]:
            if column["role"] == "meta":
                continue
            note = f"    · {column['name']} {column['type']} [{column['role']}]"
            values = samples.get(column["name"])
            if values:
                note += f" 값: {', '.join(values)}"
            elif column["comment"]:
                note += f" — {column['comment']}"
            lines.append(note)
        catalog = table.get("catalog") or []
        if catalog:
            pairs = ", ".join(f"{item['key']}={item['label']}" for item in catalog)
            lines.append(f"    · 코드→이름: {pairs}")
        if facts:
            lines.append(f"    · 집계 대상(fact): {', '.join(facts)}")
    return "\n".join(lines)


def _rows(client: Any, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    result = client.query(sql, parameters=parameters) if parameters else client.query(sql)
    columns = list(getattr(result, "column_names", []) or [])
    return [dict(zip(columns, row, strict=False)) for row in result.result_rows]


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def _short_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ")
    marker = "DB::Exception:"
    if marker in text:
        text = text.split(marker, 1)[1]
    return text.strip()[:300]
