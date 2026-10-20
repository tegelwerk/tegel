"""The data-source call follows whichever signature the installed Tiled defines."""

from pathlib import Path

from tegel.register.tiled._compat import generate_data_sources


class Tiled0218:
    def generate_data_sources(self, mimetype, dict_or_none, item, is_directory):
        return [("0.2.18", mimetype, dict_or_none, item, is_directory)]


class Tiled0219:
    def generate_data_sources(self, mimetype, item, is_directory, size=None, parameters=None):
        return [("0.2.19", mimetype, item, is_directory, size, parameters)]


def test_old_signature_gets_dict_or_none(tmp_path: Path) -> None:
    f = tmp_path / "a.csv"
    f.write_text("x,y\n1,2\n")
    marker = object()
    (call,) = generate_data_sources(Tiled0218(), "text/csv", f, False, marker)
    assert call == ("0.2.18", "text/csv", marker, f, False)


def test_new_signature_gets_item_and_size(tmp_path: Path) -> None:
    f = tmp_path / "a.csv"
    f.write_text("x,y\n1,2\n")
    (call,) = generate_data_sources(Tiled0219(), "text/csv", f, False, object())
    assert call == ("0.2.19", "text/csv", f, False, f.stat().st_size, None)


def test_new_signature_gets_parameters(tmp_path: Path) -> None:
    f = tmp_path / "a.h5"
    f.write_bytes(b"x")
    (call,) = generate_data_sources(
        Tiled0219(), "application/x-hdf5", f, False, object(), {"dataset": "entry/data"}
    )
    assert call[5] == {"dataset": "entry/data"}


def test_directory_has_no_size(tmp_path: Path) -> None:
    (call,) = generate_data_sources(Tiled0219(), "application/x-zarr", tmp_path, True, object())
    assert call[4] is None


def test_installed_tiled_csv_adapter(tmp_path: Path) -> None:
    from tiled.adapters.csv import CSVAdapter

    f = tmp_path / "a.csv"
    f.write_text("x,y\n1,2\n3,4\n")
    adapter = CSVAdapter.from_uris(f.as_uri())
    (source,) = generate_data_sources(adapter, "text/csv", f, False, lambda s: s)
    assert source.assets[0].data_uri.endswith("a.csv")
