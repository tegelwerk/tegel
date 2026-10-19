"""ACQUIRE transfers a Globus path onto the landing collection, then renames the local file.

The SDK is imported only while building a transfer client.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from tegel.register._uriparse import split_uri
from tegel.register.bind import Acquisition, BinderPlan, Mode
from tegel.register.context import BindContext
from tegel.register.errors import (
    AcquireError,
    AcquireTimeoutError,
    AuthenticationError,
    AuthorizationError,
    BindError,
    InvalidURIError,
    PluginError,
    ResourceUnavailableError,
    SecretLookupError,
    SiteConfigError,
    URIResolverError,
)
from tegel.register.redaction import sanitize_exception
from tegel.register.site import Source, select_secret_id

_TRANSFER_SCOPE = "urn:globus:auth:scope:transfer.api.globus.org:all"
_POLL_SECONDS = 2
_MODES = (Mode.EXISTING, Mode.REFERENCE, Mode.PROXY, Mode.ACQUIRE)


@dataclass(frozen=True)
class _Item:
    source_path: str
    destination_path: str
    recursive: bool


@dataclass(frozen=True)
class _Submission:
    source_endpoint: str
    destination_endpoint: str
    sync_level: str
    verify_checksum: bool
    notify_on_succeeded: bool
    notify_on_failed: bool
    notify_on_inactive: bool
    items: tuple[_Item, ...]


class GlobusBinder:
    api_version = 2
    protocol = "globus"
    opaque_payload = False

    def validate_source(self, source: Source) -> None:
        collection = source.params.get("collection")
        if not isinstance(collection, str) or not collection:
            raise SiteConfigError(f"sources.{source.scheme}.collection is required")

    def feasible_modes(
        self, uri: str, source: Source | None, into: Any, ctx: BindContext
    ) -> list[tuple[Mode, str | None]]:
        del uri, into
        if source is None or not isinstance(source.params.get("collection"), str):
            missing = "globus URIs need a source collection"
            return [(mode, missing) for mode in _MODES]
        landing = ctx.site.landing if ctx.site is not None else None
        ready = landing is not None and bool(landing.globus_collection) and landing.globus_path is not None
        acquire = None if ready else "landing.globus is not configured"
        reasons = {
            Mode.EXISTING: "globus URIs are not nodes on the target server",
            Mode.REFERENCE: "globus URIs are not files in readable storage",
            Mode.PROXY: "globus URIs are not an upstream Tiled node",
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
            raise BindError(f"globus binder cannot plan mode {mode.value}")
        if source is None:
            raise BindError("globus URIs need a source collection")
        path, recursive = _split_collection(uri)
        size = _reject_directory(ctx, source, path, recursive)
        _parent, name = _parent_name(path)
        if not name:
            raise BindError("derived key '' is not a valid Tiled key; pass key=")
        if ctx.site is None or ctx.site.landing is None:
            raise SiteConfigError("landing.globus is not configured")
        landing = ctx.site.landing_path("globus", uri, name)
        return BinderPlan(
            acquisition=Acquisition(
                protocol="globus",
                resolved_uri=uri,
                landing_local=str(landing),
                recursive=recursive,
                expected_size=size,
            ),
            size_bytes=size,
        )

    def acquire(self, acquisition: Acquisition, ctx: BindContext, *, timeout: float | None) -> Path:
        if ctx.site is None or ctx.site.landing is None or not ctx.site.landing.globus_collection:
            raise SiteConfigError("landing.globus is not configured")
        source = ctx.site.source_for(acquisition.resolved_uri)
        if source is None:
            raise BindError("globus URIs need a source collection")
        path, recursive = _split_collection(acquisition.resolved_uri)
        values = _remember_source(ctx, source, path)
        dest = Path(acquisition.landing_local)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".partial")
        if tmp.exists():
            tmp.unlink()
        remote_tmp = _remote_path(ctx, tmp)
        submission = _Submission(
            source_endpoint=str(source.params["collection"]),
            destination_endpoint=str(ctx.site.landing.globus_collection),
            sync_level="checksum",
            verify_checksum=True,
            notify_on_succeeded=False,
            notify_on_failed=False,
            notify_on_inactive=False,
            items=(_Item(source_path=path, destination_path=remote_tmp, recursive=recursive),),
        )
        client = _client(ctx, source, path, values)
        try:
            result = _submit(client, submission, values=values, uri=acquisition.resolved_uri)
            task_id = _task_id(result)
            _wait(client, task_id, timeout, values=values, uri=acquisition.resolved_uri)
            if not tmp.exists():
                raise SiteConfigError(
                    "transfer succeeded but the local landing file is missing; "
                    "landing.local and landing.globus must be the same storage"
                )
            os.replace(tmp, dest)
        except Exception:
            if tmp.exists():
                tmp.unlink()
            raise
        return dest


def _remember_source(ctx: BindContext, source: Source, path: str) -> set[str]:
    """Every secret value named by the source. Later lookups add; nothing is dropped."""
    values: set[str] = set()
    if ctx.secrets is None:
        return values
    ids: list[str] = []
    default = source.params.get("secret_id")
    if isinstance(default, str) and default:
        ids.append(default)
    prefixes = source.params.get("secrets") or {}
    if isinstance(prefixes, dict):
        ids.extend(str(item) for item in prefixes.values() if item)
    selected = select_secret_id("/" + path.strip("/"), default=default, by_prefix=prefixes if isinstance(prefixes, dict) else {})
    if isinstance(selected, str) and selected and selected not in ids:
        ids.append(selected)
    for secret_id in ids:
        try:
            secret = ctx.secrets.get_secret(secret_id)
        except Exception:
            continue
        if isinstance(secret, Mapping):
            for value in secret.values():
                if isinstance(value, str) and value:
                    values.add(value)
    return values


def _client(ctx: BindContext, source: Source, path: str, values: set[str]) -> Any:
    collection = str(source.params["collection"])
    prefixes = source.params.get("secrets") or {}
    if not isinstance(prefixes, dict):
        prefixes = {}
    secret_id = select_secret_id("/" + path.strip("/"), default=source.params.get("secret_id"), by_prefix=prefixes)

    def factory() -> Any:
        if not secret_id or ctx.secrets is None:
            raise SecretLookupError("globus binder requires a secret")
        secret = ctx.secrets.get_secret(secret_id)
        if isinstance(secret, Mapping):
            for value in secret.values():
                if isinstance(value, str) and value:
                    values.add(value)
        return _transfer_client(secret)

    return ctx.cache(("globus", collection, secret_id), factory)


def _remote_path(ctx: BindContext, local: Path) -> str:
    landing = ctx.site.landing
    root = Path(landing.local)
    try:
        relative = local.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise SiteConfigError(f"landing path {local} is outside landing.local") from exc
    remote = PurePosixPath(landing.globus_path) / PurePosixPath(relative.as_posix())
    return remote.as_posix()


def _reject_directory(ctx: BindContext, source: Source, path: str, recursive: bool) -> int | None:
    """Refuse a directory without ``?recursive``; return the file's size when the listing gives it."""
    if recursive or ctx.secrets is None:
        return None
    try:
        values: set[str] = set()
        client = _client(ctx, source, path, values)
        parent, name = _parent_name(path)
        rows = _rows(client.operation_ls(str(source.params["collection"]), path=parent, filter=f"name:={name}"))
    except Exception:
        return None
    size = None
    for row in rows:
        if isinstance(row, dict) and row.get("name") == name and row.get("type") == "dir":
            raise BindError("directory needs ?recursive")
        if isinstance(row, dict) and row.get("name") == name and isinstance(row.get("size"), int):
            size = row["size"]
    return size


def _submit(client: Any, submission: _Submission, *, values: set[str], uri: str) -> object:
    def _send() -> object:
        module = type(client).__module__
        if module.startswith("globus_sdk"):
            sdk = _sdk()
            data = sdk.TransferData(
                submission.source_endpoint,
                submission.destination_endpoint,
                sync_level=submission.sync_level,
                verify_checksum=submission.verify_checksum,
                notify_on_succeeded=submission.notify_on_succeeded,
                notify_on_failed=submission.notify_on_failed,
                notify_on_inactive=submission.notify_on_inactive,
            )
            for item in submission.items:
                data.add_item(item.source_path, item.destination_path, recursive=item.recursive)
            return client.submit_transfer(data)
        return client.submit_transfer(submission)

    return _call(_send, values=values, uri=uri)


def _wait(client: Any, task_id: str, timeout: float | None, *, values: set[str], uri: str) -> None:
    """Wait with integer ``timeout`` and ``polling_interval`` of at least 1.

    ``timeout=None`` repeats a 3600-second wait until the task finishes.
    """

    def _run() -> None:
        if timeout is None:
            while True:
                finished = client.task_wait(task_id, timeout=3600, polling_interval=_POLL_SECONDS)
                if finished:
                    break
        else:
            finished = client.task_wait(
                task_id, timeout=_timeout_arg(timeout), polling_interval=_POLL_SECONDS
            )
            if not finished:
                _cancel(client, task_id)
                raise AcquireTimeoutError("transfer timed out")
        status = _task_status(client.get_task(task_id))
        if status == "SUCCEEDED":
            return
        if status == "FAILED":
            raise AcquireError(_fatal(client, task_id, values))
        raise AcquireError(_scrub(f"transfer ended {status or 'unknown'}", values))

    _call(_run, values=values, uri=uri)


def _timeout_arg(timeout: float) -> int:
    seconds = int(timeout)
    return seconds if seconds >= 1 else 1


def _sdk() -> Any:
    try:
        import globus_sdk
    except ImportError as exc:
        raise PluginError("globus binder requires the globus extra") from exc
    return globus_sdk


def _transfer_client(secret: Mapping[str, str]) -> Any:
    client_id = secret.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise SecretLookupError("secret map is missing client_id")
    refresh = secret.get("refresh_token")
    client_secret = secret.get("client_secret")
    if not (isinstance(refresh, str) and refresh) and not (
        isinstance(client_secret, str) and client_secret
    ):
        raise SecretLookupError("secret map is missing refresh_token or client_secret")
    sdk = _sdk()
    if isinstance(refresh, str) and refresh:
        authorizer = sdk.RefreshTokenAuthorizer(refresh, sdk.NativeAppAuthClient(client_id))
    else:
        confidential = sdk.ConfidentialAppAuthClient(client_id, client_secret)
        authorizer = sdk.ClientCredentialsAuthorizer(confidential, scopes=_TRANSFER_SCOPE)
    return sdk.TransferClient(authorizer=authorizer)


def _split_collection(uri: str) -> tuple[str, bool]:
    """Return the decoded collection path and whether ``?recursive`` was set."""
    try:
        body = split_uri(uri).body
    except ValueError as exc:
        raise InvalidURIError(str(exc)) from None
    path, sep, query = body.partition("?")
    if sep and query != "recursive":
        raise InvalidURIError("globus URI query must be the recursive flag")
    if path.startswith("//"):
        parsed = urlparse("http:" + path)
        if parsed.netloc:
            raise InvalidURIError("globus URI has no authority")
        path = parsed.path
    if not path.startswith("/"):
        raise InvalidURIError("globus path must be absolute")
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if path == "/":
        return "/", bool(sep)
    segments = path.split("/")[1:]
    if any(segment == "" for segment in segments):
        raise InvalidURIError(f"empty path segment in {uri!r}")
    decoded = [unquote(segment) for segment in segments]
    if any(segment == "" for segment in decoded):
        raise InvalidURIError(f"empty path segment in {uri!r}")
    return "/" + "/".join(decoded), bool(sep)


def _parent_name(path: str) -> tuple[str, str]:
    if path == "/":
        return "/", ""
    parent, _, name = path.rpartition("/")
    return parent or "/", name


def _rows(payload: object) -> list:
    if isinstance(payload, dict):
        data = payload.get("DATA", payload.get("data", []))
        return list(data or [])
    data = getattr(payload, "data", None)
    if isinstance(data, dict):
        return list(data.get("DATA", []) or [])
    if isinstance(payload, list):
        return payload
    return []


def _task_id(result: object) -> str:
    if isinstance(result, dict) and "task_id" in result:
        return str(result["task_id"])
    data = getattr(result, "data", None)
    if isinstance(data, dict) and "task_id" in data:
        return str(data["task_id"])
    raise AcquireError("transfer submit did not return a task id")


def _task_status(payload: object) -> str:
    if isinstance(payload, dict):
        return str(payload.get("status", ""))
    data = getattr(payload, "data", None)
    if isinstance(data, dict):
        return str(data.get("status", ""))
    return ""


def _classify(exc: BaseException) -> tuple[str, tuple]:
    info = getattr(exc, "info", None)
    scopes: tuple = ()
    consent = False
    if isinstance(info, dict):
        consent = bool(info.get("consent_required"))
        scopes = tuple(info.get("required_scopes") or ())
    elif info is not None:
        consent = bool(getattr(info, "consent_required", False))
        scopes = tuple(getattr(info, "required_scopes", ()) or ())
    code = getattr(exc, "code", None)
    if consent or code == "ConsentRequired":
        return "consent", scopes
    if type(exc).__name__ == "NetworkError":
        return "unavailable", ()
    status = getattr(exc, "http_status", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status == 401:
        return "auth", ()
    if status == 403:
        return "authz", ()
    if status == 404:
        return "missing", ()
    if isinstance(status, int) and status >= 500:
        return "unavailable", ()
    return "default", ()


def _cancel(client: Any, task_id: str) -> None:
    try:
        client.cancel_task(task_id)
    except Exception:
        return


def _fatal(client: Any, task_id: str, values: set[str]) -> str:
    try:
        events = client.task_event_list(task_id)
    except Exception:
        return "transfer failed"
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("is_error") or event.get("is_fatal"):
            detail = event.get("description") or event.get("details") or "transfer failed"
            return _scrub(str(detail), values)
    return "transfer failed"


def _scrub(message: str, values: set[str]) -> str:
    text = message
    for value in values:
        if len(value) >= 6 and value in text:
            text = text.replace(value, "<redacted>")
    return text


def _call(fn, *, values: set[str], uri: str):
    try:
        return fn()
    except URIResolverError as exc:
        scrubbed = _scrub(str(exc), values)
        if scrubbed != str(exc):
            raise type(exc)(scrubbed) from None
        raise
    except Exception as exc:
        kind, scopes = _classify(exc)
        if kind == "consent":
            listed = ", ".join(str(scope) for scope in scopes) if scopes else "(unknown)"
            message = f"consent required; required_scopes: {listed}. rerun login.py"
            wrapper: type[Exception] = AuthorizationError
        elif kind == "auth":
            message = "globus authentication failed"
            wrapper = AuthenticationError
        elif kind == "authz":
            message = "globus authorization failed"
            wrapper = AuthorizationError
        elif kind == "missing":
            message = "globus path was not found"
            wrapper = BindError
        elif kind == "unavailable":
            message = "globus transfer service unavailable"
            wrapper = ResourceUnavailableError
        else:
            message = str(exc) or "transfer failed"
            wrapper = AcquireError
        raise sanitize_exception(exc, wrapper_type=wrapper, message=_scrub(message, values), opaque_uris=(uri,)) from None
