"""ACQUIRE downloads one named file from a Zenodo record.

The ``https`` scheme is not claimed here. ``https://zenodo.org/records/...``
is accepted only when a source sets ``host: zenodo.org``.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from tegel.register._uriparse import split_uri
from tegel.register.bind import Acquisition, BinderPlan, Mode
from tegel.register.context import BindContext
from tegel.register.errors import AcquireError, BindError, InvalidURIError, SiteConfigError
from tegel.register.site import Source

_RECORD_HOST = "zenodo.org"
_API = f"https://{_RECORD_HOST}"
_USER_AGENT = "tegel-register/0.1"
_MODES = (Mode.EXISTING, Mode.REFERENCE, Mode.PROXY, Mode.ACQUIRE)


class ZenodoBinder:
    api_version = 2
    protocol = "zenodo"
    opaque_payload = False

    def validate_source(self, source: Source) -> None:
        if source.scheme == "https" and source.params.get("host") != _RECORD_HOST:
            raise SiteConfigError(f"sources.{source.scheme}.host must be {_RECORD_HOST}")

    def feasible_modes(
        self, uri: str, source: Source | None, into: Any, ctx: BindContext
    ) -> list[tuple[Mode, str | None]]:
        del uri, source, into
        acquire = None if ctx.site is not None and ctx.site.landing is not None else "landing is not configured"
        reasons = {
            Mode.EXISTING: "zenodo records are not nodes on the target server",
            Mode.REFERENCE: "zenodo records are not files in readable storage",
            Mode.PROXY: "zenodo records are not an upstream Tiled node",
            Mode.ACQUIRE: acquire,
        }
        return [(mode, reasons[mode]) for mode in _MODES]

    def plan(
        self,
        uri: str,
        source: Source | None,
        into: Any,
        ctx: BindContext,
        *,
        mode: Mode,
        key: str | None,
    ) -> BinderPlan:
        del into, key
        if mode is not Mode.ACQUIRE:
            raise BindError(f"zenodo binder cannot plan mode {mode.value}")
        if ctx.site is None or ctx.site.landing is None:
            raise SiteConfigError("landing is not configured")
        record_id, filename = _parse_record_uri(uri, source)
        landing = ctx.site.landing_path("zenodo", uri, filename)
        size, md5, notes = None, None, ()
        try:  # the record's metadata gives the size and checksum; planning still works without it
            meta = _fetch_record(record_id, filename)
            size, md5 = int(meta["size_bytes"]), str(meta["md5"])
        except Exception as exc:
            notes = (f"size unknown: record metadata unavailable ({type(exc).__name__})",)
        return BinderPlan(
            acquisition=Acquisition(
                protocol="zenodo",
                resolved_uri=uri,
                landing_local=str(landing),
                recursive=False,
                expected_size=size,
                expected_md5=md5,
            ),
            notes=notes,
            size_bytes=size,
        )

    def acquire(self, acquisition: Acquisition, ctx: BindContext, *, timeout: float | None) -> Path:
        del timeout
        source = ctx.site.source_for(acquisition.resolved_uri) if ctx.site is not None else None
        record_id, filename = _parse_record_uri(acquisition.resolved_uri, source)
        meta = _fetch_record(record_id, filename)
        dest = Path(acquisition.landing_local)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".partial")
        if tmp.exists():
            tmp.unlink()
        try:
            with tmp.open("wb") as handle:
                _stream_content(meta["content_uri"], meta["size_bytes"], meta["md5"], handle.write)
            os.replace(tmp, dest)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return dest


def _parse_record_uri(uri: str, source: Source | None) -> tuple[str, str]:
    """Return ``(record_id, filename)`` for a zenodo or mapped https URI."""
    try:
        parts = split_uri(uri)
    except ValueError as exc:
        raise InvalidURIError(str(exc)) from None
    if parts.scheme == "https":
        parsed = urllib.parse.urlparse(uri)
        host = (parsed.hostname or "").lower()
        mapped = str(source.params.get("host") or "") if source is not None else ""
        if mapped != _RECORD_HOST or host != _RECORD_HOST:
            raise InvalidURIError(
                f"https zenodo URIs require a source host {_RECORD_HOST}: {uri!r}"
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise InvalidURIError(f"Zenodo URI must not carry userinfo, a query, or a fragment: {uri!r}")
        path = parsed.path
    elif parts.scheme == "zenodo" or (source is not None and source.protocol == "zenodo"):
        body = parts.body
        if body.startswith("//"):
            raise InvalidURIError(f"zenodo URI has no authority: {uri!r}")
        path = body if body.startswith("/") else "/" + body
        path = path.split("?", 1)[0]
    else:
        raise InvalidURIError(f"not a zenodo URI: {uri!r}")
    match_path = path.rstrip("/")
    prefix = "/records/"
    if not match_path.startswith(prefix):
        raise InvalidURIError(f"Zenodo URI path must be /records/<id>/<filename>: {uri!r}")
    rest = match_path[len(prefix):]
    record_id, sep, filename = rest.partition("/")
    if not sep or not record_id.isdigit() or not filename or "/" in filename:
        raise InvalidURIError(f"Zenodo URI path must be /records/<id>/<filename>: {uri!r}")
    return record_id, filename


def _opener(allowed_host: str) -> urllib.request.OpenerDirector:
    class _StayOnHost(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
            if _host(newurl) != allowed_host:
                raise AcquireError(f"refusing redirect off {allowed_host}: {newurl}")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(_StayOnHost())


def _request(url: str, *, accept: str | None = None) -> urllib.request.Request:
    headers = {"User-Agent": _USER_AGENT}
    if accept is not None:
        headers["Accept"] = accept
    return urllib.request.Request(url, headers=headers)


def _host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _fetch_record(record_id: str, filename: str) -> dict[str, Any]:
    url = f"{_API.rstrip('/')}/api/records/{record_id}"
    opener = _opener(_host(_API))
    try:
        with opener.open(_request(url, accept="application/json"), timeout=60) as resp:
            payload = json.load(resp)
    except AcquireError:
        raise
    except urllib.error.HTTPError as exc:
        raise AcquireError(f"Zenodo record {record_id} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise AcquireError(f"Zenodo record {record_id} unreachable: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise AcquireError(f"Zenodo record {record_id} did not return JSON") from exc
    files = payload.get("files")
    if not isinstance(files, list):
        raise AcquireError(f"Zenodo record {record_id} has no files")
    matches = [entry for entry in files if isinstance(entry, dict) and entry.get("key") == filename]
    if len(matches) != 1:
        raise AcquireError(f"Zenodo record {record_id} has no file {filename!r}")
    entry = matches[0]
    try:
        size_bytes = int(entry["size"])
        checksum = str(entry["checksum"])
        content_uri = str(entry["links"]["self"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AcquireError(f"Zenodo record {record_id} is missing file metadata") from exc
    prefix = "md5:"
    if not checksum.lower().startswith(prefix) or len(checksum) != len(prefix) + 32:
        raise AcquireError(f"Zenodo record {record_id} has no md5 checksum")
    md5 = checksum[len(prefix):].lower()
    if any(char not in "0123456789abcdef" for char in md5):
        raise AcquireError(f"Zenodo record {record_id} md5 is not hexadecimal")
    if _host(content_uri) != _host(_API):
        raise AcquireError(f"Zenodo record {record_id} content URL is not on {_host(_API)}")
    return {"filename": filename, "size_bytes": size_bytes, "md5": md5, "content_uri": content_uri}


def _stream_content(url: str, expected_size: int, expected_md5: str, write: Any) -> tuple[int, str]:
    """Stream ``url`` through ``write(chunk)``. Fail if size or MD5 disagrees."""
    opener = _opener(_host(url))
    digest = hashlib.md5()
    total = 0
    try:
        with opener.open(_request(url), timeout=120) as resp:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected_size:
                    raise AcquireError(f"download exceeded record size {expected_size} bytes")
                digest.update(chunk)
                write(chunk)
    except AcquireError:
        raise
    except urllib.error.HTTPError as exc:
        raise AcquireError(f"Zenodo file download returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise AcquireError(f"Zenodo file download failed: {exc.reason}") from exc
    got = digest.hexdigest()
    if total != expected_size or got != expected_md5.lower():
        raise AcquireError(
            f"download does not match the Zenodo record: "
            f"size {total} (expected {expected_size}), md5 {got} (expected {expected_md5})"
        )
    return total, got
