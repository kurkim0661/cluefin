"""저장소 루트의 `.env`를 프로세스 환경으로 올린다.

수집기·에이전트·웹은 모두 `os.getenv`로 자격증명을 읽는데, 콘솔에서 `uv run ...`으로 실행하면
`.env`가 자동으로 로드되지 않는다. 그래서 `.env`에 값을 넣어도 "설정이 없습니다"가 뜬다.
진입점에서 이 함수를 한 번 불러 그 간극을 없앤다.

실제 환경변수가 항상 이긴다. 셸에서 덮어쓴 값을 파일이 되돌리면 디버깅이 불가능해진다.
python-dotenv은 dev 의존성이라 런타임에 쓸 수 없어 직접 파싱하되, 규칙은 셸의 `source`와 맞춘다
(따옴표 벗기기, 따옴표 밖 인라인 주석 제거, `export` 접두사 허용, `${VAR}` 치환).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import MutableMapping

ENV_FILE_NAME = ".env"
ENV_PATH_VAR = "CLUEFIN_ENV_FILE"
MAX_PARENTS = 6


def find_env_file(start: Path | None = None, *, environ: MutableMapping[str, str] | None = None) -> Path | None:
    """`CLUEFIN_ENV_FILE`이 있으면 그 경로, 없으면 현재 위치부터 위로 올라가며 `.env`를 찾는다."""
    env = environ if environ is not None else os.environ
    override = env.get(ENV_PATH_VAR)
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_file() else None
    current = (start or Path.cwd()).resolve()
    for directory in (current, *list(current.parents)[:MAX_PARENTS]):
        candidate = directory / ENV_FILE_NAME
        if candidate.is_file():
            return candidate
    return None


def parse_env_file(text: str, *, environ: MutableMapping[str, str] | None = None) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        values[key] = _expand(_clean_value(value.strip()), values, environ)
    return values


def _expand(
    value: str,
    parsed: dict[str, str],
    environ: MutableMapping[str, str] | None,
) -> str:
    """`${VAR}` / `$VAR`를 이미 있는 환경변수나 앞 줄 값으로 바꾼다.

    다른 도구가 이미 export한 자격증명을 `.env`에 복사하지 않고 가리킬 수 있게 한다.
    예: `CLUEFIN_LLM_PAT=${MY_GATEWAY_TOKEN}` — 비밀을 파일에 다시 적지 않는다.
    """
    if "$" not in value:
        return value
    env = environ if environ is not None else os.environ

    def resolve(match: re.Match[str]) -> str:
        name = match.group("braced") or match.group("bare")
        if name in parsed:
            return parsed[name]
        return env.get(name, "")

    return re.sub(r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))", resolve, value)


def _clean_value(value: str) -> str:
    """따옴표를 벗기고 인라인 주석을 잘라낸다.

    `KIWOOM_ENV=dev # options: prod | dev`를 그대로 읽으면 값이 "dev # options: prod | dev"가 되어
    설정 검증이 깨진다. 셸의 `source`와 python-dotenv처럼 따옴표 밖의 ` #`부터는 주석으로 본다.
    """
    if value[:1] in {'"', "'"}:
        quote = value[0]
        closing = value.find(quote, 1)
        if closing != -1:
            return value[1:closing]
        return value[1:]
    for index in range(len(value) - 1):
        if value[index].isspace() and value[index + 1] == "#":
            return value[:index].rstrip()
    return value


def load_env_file(
    start: Path | None = None,
    *,
    override: bool = False,
    environ: MutableMapping[str, str] | None = None,
) -> tuple[str, ...]:
    """`.env`에서 비어 있는 환경변수를 채우고, 채운 키 이름만 돌려준다.

    값은 절대 반환하거나 로그로 남기지 않는다. 호출부는 이름만으로 "무엇이 로드됐는지" 알 수 있다.
    """
    env = environ if environ is not None else os.environ
    path = find_env_file(start, environ=env)
    if path is None:
        return ()
    loaded: list[str] = []
    for key, value in parse_env_file(path.read_text(encoding="utf-8"), environ=env).items():
        if not override and env.get(key):
            continue
        env[key] = value
        loaded.append(key)
    return tuple(loaded)
