from cluefin_store import __version__


def test_import_exposes_version() -> None:
    assert __version__ == "0.1.0"
