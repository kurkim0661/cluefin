from __future__ import annotations

from pathlib import Path

from cluefin_store.env import ENV_PATH_VAR, find_env_file, load_env_file, parse_env_file


def test_parse_env_file_handles_quotes_comments_and_export_prefix() -> None:
    parsed = parse_env_file(
        "\n".join(
            [
                "# comment",
                "",
                "PLAIN=value",
                'QUOTED="quoted value"',
                "SINGLE='single'",
                "export EXPORTED=exported",
                "SPACED  =  padded  ",
                "EMPTY=",
                "no_equals_here",
                "INLINE_COMMENT=dev # options: prod | dev(default)",
                'HASH_IN_QUOTES="pa#ss word" # trailing note',
                "URL=https://host/path#fragment",
            ]
        )
    )

    assert parsed == {
        "PLAIN": "value",
        "QUOTED": "quoted value",
        "SINGLE": "single",
        "EXPORTED": "exported",
        "SPACED": "padded",
        "EMPTY": "",
        # 셸의 source와 같게 따옴표 밖 인라인 주석만 잘라낸다.
        "INLINE_COMMENT": "dev",
        "HASH_IN_QUOTES": "pa#ss word",
        "URL": "https://host/path#fragment",
    }


def test_load_env_file_fills_missing_keys_and_reports_names(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("CLUEFIN_LLM_PAT=secret\nCLUEFIN_LLM_MODEL=gpt-x\n", encoding="utf-8")
    environ: dict[str, str] = {}

    loaded = load_env_file(tmp_path, environ=environ)

    assert sorted(loaded) == ["CLUEFIN_LLM_MODEL", "CLUEFIN_LLM_PAT"]
    assert environ["CLUEFIN_LLM_MODEL"] == "gpt-x"


def test_real_environment_wins_over_the_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("CLUEFIN_LLM_MODEL=from-file\n", encoding="utf-8")
    environ = {"CLUEFIN_LLM_MODEL": "from-shell"}

    loaded = load_env_file(tmp_path, environ=environ)

    # 셸에서 덮어쓴 값을 파일이 되돌리면 디버깅이 불가능해진다.
    assert loaded == ()
    assert environ["CLUEFIN_LLM_MODEL"] == "from-shell"


def test_override_flag_lets_the_file_win(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("CLUEFIN_LLM_MODEL=from-file\n", encoding="utf-8")
    environ = {"CLUEFIN_LLM_MODEL": "from-shell"}

    load_env_file(tmp_path, override=True, environ=environ)

    assert environ["CLUEFIN_LLM_MODEL"] == "from-file"


def test_empty_value_in_the_file_does_not_mask_a_real_value(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("CLUEFIN_LLM_PAT=\n", encoding="utf-8")
    environ = {"CLUEFIN_LLM_PAT": "real"}

    load_env_file(tmp_path, environ=environ)

    assert environ["CLUEFIN_LLM_PAT"] == "real"


def test_find_env_file_walks_up_from_a_subdirectory(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    nested = tmp_path / "packages" / "cluefin-store" / "src"
    nested.mkdir(parents=True)

    assert find_env_file(nested, environ={}) == (tmp_path / ".env").resolve()


def test_explicit_env_path_variable_wins(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=from-root\n", encoding="utf-8")
    custom = tmp_path / "custom.env"
    custom.write_text("A=from-custom\n", encoding="utf-8")
    environ = {ENV_PATH_VAR: str(custom)}

    load_env_file(tmp_path, environ=environ)

    assert environ["A"] == "from-custom"


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    environ: dict[str, str] = {}

    assert load_env_file(tmp_path / "empty", environ=environ) == ()
    assert environ == {}


def test_variable_expansion_points_at_an_already_exported_secret(tmp_path: Path) -> None:
    """비밀을 .env에 복사하지 않고 다른 도구가 export한 변수를 가리킬 수 있어야 한다."""
    (tmp_path / ".env").write_text("CLUEFIN_LLM_PAT=${MY_GATEWAY_TOKEN}\nMIXED=$A-suffix\nA=first\n", encoding="utf-8")
    environ = {"MY_GATEWAY_TOKEN": "real-secret"}

    load_env_file(tmp_path, environ=environ)

    assert environ["CLUEFIN_LLM_PAT"] == "real-secret"
    # 파일 앞줄에서 정의되기 전이면 빈 값이 된다(셸과 같다).
    assert environ["MIXED"] == "-suffix"
    assert environ["A"] == "first"


def test_unknown_variable_expands_to_empty_and_stays_missing(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("CLUEFIN_LLM_PAT=${NOT_SET_ANYWHERE}\n", encoding="utf-8")
    environ: dict[str, str] = {}

    load_env_file(tmp_path, environ=environ)

    # 빈 문자열이 들어가면 "설정됨"으로 오인될 수 있으니 값 판정은 호출부(strip 검사)가 맡는다.
    assert environ["CLUEFIN_LLM_PAT"] == ""
