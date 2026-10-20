"""Site file validation, path mapping, and source merge."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from tegel.register.errors import SiteConfigError
from tegel.register.site import Site, select_secret_id


def test_select_secret_id_longest_prefix():
    chosen = select_secret_id(
        "/examples/private/raw/frame.h5",
        default="tiled-public",
        by_prefix={"examples/private": "tiled-lab", "examples/private/raw": "tiled-raw"},
    )
    assert chosen == "tiled-raw"
    assert select_secret_id("/other", default="tiled-public", by_prefix={}) == "tiled-public"


def test_readable_must_be_absolute():
    with pytest.raises(SiteConfigError, match="readable\\[0\\].local"):
        Site.from_mapping({"version": 1, "readable": [{"local": "relative", "server": "/data"}]})


def test_readable_locals_must_not_nest():
    with pytest.raises(SiteConfigError, match="readable\\[0\\].local"):
        Site.from_mapping(
            {
                "version": 1,
                "readable": [
                    {"local": "/data", "server": "/srv"},
                    {"local": "/data/nested", "server": "/other"},
                ],
            }
        )


def test_readable_servers_must_not_nest():
    with pytest.raises(SiteConfigError, match="readable\\[0\\].server"):
        Site.from_mapping(
            {
                "version": 1,
                "readable": [
                    {"local": "/data/a", "server": "/srv"},
                    {"local": "/data/b", "server": "/srv/nested"},
                ],
            }
        )


def test_landing_outside_readable_is_rejected():
    with pytest.raises(SiteConfigError, match="landing.local"):
        Site.from_mapping(
            {
                "version": 1,
                "readable": [{"local": "/data", "server": "/srv"}],
                "landing": {"local": "/elsewhere/landing", "layout": "{protocol}/{sha12}/{name}"},
            }
        )


def test_layout_requires_name_and_digest():
    readable = [{"local": "/data", "server": "/srv"}]
    with pytest.raises(SiteConfigError, match="landing.layout"):
        Site.from_mapping(
            {
                "version": 1,
                "readable": readable,
                "landing": {"local": "/data/landing", "layout": "{protocol}/{name}"},
            }
        )
    with pytest.raises(SiteConfigError, match="landing.layout"):
        Site.from_mapping(
            {
                "version": 1,
                "readable": readable,
                "landing": {"local": "/data/landing", "layout": "{sha12}"},
            }
        )


def test_unknown_protocol_is_accepted_at_load():
    site = Site.from_mapping(
        {"version": 1, "sources": {"custom.scheme": {"protocol": "not-installed", "base_uri": "x"}}}
    )
    assert site.sources["custom.scheme"].protocol == "not-installed"


def test_to_server_path_symlink_dotdot_and_longest_root(tmp_path: Path):
    real = tmp_path / "real"
    deep = real / "deep"
    deep.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(deep, target_is_directory=True)
    inside = deep / "frame.h5"
    inside.write_bytes(b"h5")
    outside = tmp_path / "outside.h5"
    outside.write_bytes(b"no")
    escape = real / "escape.h5"
    escape.symlink_to(outside)
    site = Site.from_mapping(
        {
            "version": 1,
            "readable": [
                {"local": str(real), "server": "/data/real"},
                {"local": str(alias), "server": "/data/alias"},
            ],
        }
    )
    assert site.to_server_path(inside) == PurePosixPath("/data/alias/frame.h5")
    assert site.to_server_path(real / ".." / outside.name) is None
    assert site.to_server_path(escape) is None
    assert site.to_server_path(inside).as_posix().startswith("/data/alias")


def test_server_root_is_not_resolved(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    data = real / "a.txt"
    data.write_text("a")
    server_link = tmp_path / "srvview"
    server_link.symlink_to(real, target_is_directory=True)
    site = Site.from_mapping(
        {"version": 1, "readable": [{"local": str(real), "server": str(server_link)}]}
    )
    mapped = site.to_server_path(data)
    assert mapped == PurePosixPath(str(server_link)) / "a.txt"
    assert "real" not in mapped.as_posix() or str(server_link) in mapped.as_posix()


def test_site_file_resolves_relative_paths_without_following_symlinks(tmp_path):
    real = tmp_path / "real"
    incoming = real / "incoming"
    incoming.mkdir(parents=True)
    link = tmp_path / "srvview"
    link.symlink_to(real, target_is_directory=True)
    site_file = tmp_path / "site.yaml"
    site_file.write_text(
        "version: 1\n"
        "readable:\n"
        "  - local: real\n"
        "    server: srvview\n"
        "landing:\n"
        "  local: real/incoming\n"
        "  layout: '{protocol}/{sha12}/{name}'\n",
        encoding="utf-8",
    )
    site = Site.load(str(site_file))
    assert site.readable[0].local == PurePosixPath(str(real))
    assert site.readable[0].server == PurePosixPath(str(link))
    assert site.landing is not None
    assert site.landing.local == PurePosixPath(str(incoming))


def test_explicit_path_is_not_merged_with_user_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".config" / "tegel-register"
    config.mkdir(parents=True)
    (config / "site.yaml").write_text(
        "version: 1\nsources:\n  user.scheme:\n    protocol: zenodo\n",
        encoding="utf-8",
    )
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        "version: 1\nsources:\n  only.here:\n    protocol: tiled\n    base_uri: https://example.test\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TEGEL_REGISTER_SITE", raising=False)
    site = Site.load(str(explicit))
    assert "user.scheme" not in site.sources
    assert site.sources["only.here"].protocol == "tiled"
    user = Site.load()
    assert "user.scheme" in user.sources
    assert "only.here" not in user.sources


def test_entry_point_sources_merge_under_the_file(tmp_path, monkeypatch):
    def sources():
        return {
            "kept.file": {"protocol": "zenodo"},
            "from.ep": {"protocol": "globus", "collection": "abc"},
        }

    monkeypatch.setattr("tegel.register.binders._plugins.load_source_entries", sources)
    path = tmp_path / "site.yaml"
    path.write_text(
        "version: 1\nsources:\n  kept.file:\n    protocol: file\n    note: wins\n",
        encoding="utf-8",
    )
    site = Site.load(str(path))
    assert site.sources["kept.file"].protocol == "file"
    assert site.sources["kept.file"].params["note"] == "wins"
    assert site.sources["from.ep"].protocol == "globus"


def test_landing_path_layout(tmp_path: Path):
    root = tmp_path / "data"
    landing = root / "landing"
    root.mkdir()
    site = Site.from_mapping(
        {
            "version": 1,
            "readable": [{"local": str(root), "server": "/srv"}],
            "landing": {"local": str(landing), "layout": "{protocol}/{sha12}/{name}"},
        }
    )
    path = site.landing_path("file", "file:///tmp/a.bin", "a.bin")
    assert path.parent.parent.parent == landing
    assert path.name == "a.bin"
    assert len(path.parent.name) == 12
