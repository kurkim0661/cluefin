from __future__ import annotations

import importlib
import sys


def test_settings_ignore_unknown_shared_env_keys(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        "TOSS_CLIENT_ID=test-client\nTOSS_CLIENT_SECRET=test-secret\nKIS_ENV=dev\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("cluefin_cli.config.settings", None)

    module = importlib.import_module("cluefin_cli.config.settings")

    assert module.settings.kis_env == "dev"
