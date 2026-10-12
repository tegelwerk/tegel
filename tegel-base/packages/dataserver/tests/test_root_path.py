"""Optional root path is passed only when the environment sets it."""

from tegel.dataserver.launch import root_path


def test_root_path_is_unset_by_default(monkeypatch) -> None:
    monkeypatch.delenv("TEGEL_ROOT_PATH", raising=False)
    assert root_path() is None


def test_root_path_reads_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("TEGEL_ROOT_PATH", "/data")
    assert root_path() == "/data"
