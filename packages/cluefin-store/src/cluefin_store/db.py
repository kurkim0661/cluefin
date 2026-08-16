from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from cluefin_store.schema import SCHEMA_STATEMENTS


class ClickHouseClientProtocol(Protocol):
    def command(self, sql: str):
        pass


@dataclass(frozen=True, slots=True)
class ClickHouseSettings:
    host: str = "localhost"
    port: int = 8123
    username: str = "default"
    password: str = ""
    secure: bool = False

    @classmethod
    def from_env(cls) -> "ClickHouseSettings":
        return cls(
            host=os.getenv("CLUEFIN_CLICKHOUSE_HOST", "localhost"),
            port=int(os.getenv("CLUEFIN_CLICKHOUSE_PORT", "8123")),
            username=os.getenv("CLUEFIN_CLICKHOUSE_USER", "default"),
            password=os.getenv("CLUEFIN_CLICKHOUSE_PASSWORD", ""),
            secure=os.getenv("CLUEFIN_CLICKHOUSE_SECURE", "0").lower() in {"1", "true", "yes", "on"},
        )


class ClickHouseStore:
    def __init__(
        self, settings: ClickHouseSettings | None = None, client: ClickHouseClientProtocol | None = None
    ) -> None:
        self.settings = settings or ClickHouseSettings.from_env()
        self._client = client

    def client(self) -> ClickHouseClientProtocol:
        if self._client is None:
            import clickhouse_connect

            self._client = clickhouse_connect.get_client(
                host=self.settings.host,
                port=self.settings.port,
                username=self.settings.username,
                password=self.settings.password,
                secure=self.settings.secure,
            )
        return self._client

    def apply_schema(self) -> int:
        client = self.client()
        for statement in SCHEMA_STATEMENTS:
            client.command(statement)
        return len(SCHEMA_STATEMENTS)
