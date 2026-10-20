"""RFC 3986 §3.1 scheme grammar and URI splitting."""

from __future__ import annotations

import pytest

from tegel.register import Context, InvalidURIError
from tegel.register._uriparse import scheme_normalized, split_uri
from tegel.register.redaction import redact_uri
from tegel.register.site import Site

INVALID_EVIDENCE = [
    r"C:\data\x.tif",
    "1234:foo",
    "a b:foo",
    "/etc/passwd:x",
    "..:x",
]


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TEGEL_REGISTER_SITE", raising=False)
    return Context(site=Site.from_mapping({}))


@pytest.mark.parametrize("uri", INVALID_EVIDENCE)
def test_invalid_scheme_split_uri(uri: str):
    with pytest.raises(ValueError):
        split_uri(uri)


@pytest.mark.parametrize("uri", INVALID_EVIDENCE)
def test_invalid_scheme_resolve(uri: str, ctx):
    with pytest.raises(InvalidURIError):
        ctx.resolve_chain(uri)


def test_split_uri_file():
    parts = split_uri("file:///a")
    assert parts.scheme == "file"
    assert parts.body == "///a"
    assert parts.fragment is None


def test_split_uri_scheme_chars():
    parts = split_uri("a+b-c.d:x")
    assert parts.scheme == "a+b-c.d"
    assert parts.body == "x"


def test_split_uri_scheme_case():
    parts = split_uri("HTTP://x")
    assert parts.scheme == "http"
    assert parts.raw_scheme == "HTTP"
    assert parts.body == "//x"


def test_split_uri_fragment():
    parts = split_uri("x:a#b")
    assert parts.body == "a"
    assert parts.fragment == "b"


def test_split_uri_empty_fragment():
    parts = split_uri("x:a#")
    assert parts.fragment == ""


def test_split_uri_no_fragment():
    parts = split_uri("x:a")
    assert parts.fragment is None


def test_split_uri_fragment_with_hash():
    parts = split_uri("x:a#b#c")
    assert parts.fragment == "b#c"


def test_scheme_normalized():
    assert scheme_normalized("NS:ABC") == "ns:ABC"


def test_redact_uri_malformed():
    assert redact_uri("not a uri") == "<invalid-uri>"
