"""The ingest error log reader."""

import json
from pathlib import Path

from tegel.actions.errorlog import ErrorLog
from tegel.actions.errors import main


def _write(log: Path) -> None:
    ErrorLog(log, run_id="run-old", mode="skip").record(
        stage="check", path="/records/a.npy", exc=ValueError("old problem"), plugin_id="p"
    )
    newer = ErrorLog(log, run_id="run-new", mode="skip")
    newer.record(stage="id", path="/records/b.npy", exc=ValueError("no id"), plugin_id="p")
    newer.record(
        stage="duplicate",
        path="/records/c.npy",
        exc=ValueError("already staged"),
        plugin_id="p",
        resource_id="rec-c",
    )


def test_errors_reader_table_last_and_json(tmp_path: Path, capsys, monkeypatch) -> None:
    log = tmp_path / "ingest-errors.jsonl"
    _write(log)

    assert main(["--file", str(log)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ["time", "stage", "path", "message"]
    assert len(out) == 4
    assert "/records/a.npy" in out[1] and out[1].endswith("old problem")

    assert main(["--file", str(log), "--last"]) == 0
    out = capsys.readouterr().out
    assert "old problem" not in out
    assert "no id" in out and "already staged" in out

    assert main(["--file", str(log), "--last", "--stage", "duplicate"]) == 0
    [row] = capsys.readouterr().out.splitlines()[1:]
    assert "duplicate" in row and "/records/c.npy" in row

    assert main(["--file", str(log), "--run", "run-old", "--json"]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed == [log.read_text(encoding="utf-8").splitlines()[0]]
    assert json.loads(printed[0])["run_id"] == "run-old"

    monkeypatch.setenv("TEGEL_OUTPUT", str(tmp_path))
    assert main(["--stage", "write"]) == 0
    assert capsys.readouterr().out == "no errors\n"

    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert main(["--file", str(empty)]) == 0
    assert capsys.readouterr().out == "no errors\n"
    assert main(["--file", str(tmp_path / "missing.jsonl")]) == 0
    assert capsys.readouterr().out == "no errors\n"
