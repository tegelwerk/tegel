"""Upstream Tiled servers that remote data parts may point at.

``TEGEL_UPSTREAMS`` names a YAML file. Without it no remote part is allowed.
Secrets are read from the environment variables the file names, never from
the file. Only httpx is used; Tiled is not imported.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import AsyncGenerator, Generator, Mapping
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UPSTREAMS_ENV = "TEGEL_UPSTREAMS"
TIMEOUT_ENV = "TEGEL_UPSTREAM_TIMEOUT"
DEFAULT_TIMEOUT = 10.0


class Upstream(BaseModel):
    """One upstream Tiled server and how Tegel authenticates to it."""

    model_config = ConfigDict(extra="forbid")

    origin: str = Field(min_length=1)
    api_prefix: str = "/api/v1"
    auth: Literal["none", "api_key", "client_credentials"] = "none"
    api_key_env: str | None = None
    token_url: str | None = None
    client_id: str | None = None
    client_secret_env: str | None = None
    scope: str | None = None

    @field_validator("origin")
    @classmethod
    def _origin(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError(f"upstream origin must be http(s)://host[:port]: {value}")
        if parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise ValueError(f"upstream origin must not carry a path: {value}")
        return f"{parts.scheme}://{parts.netloc.lower()}"

    @field_validator("api_prefix")
    @classmethod
    def _prefix(cls, value: str) -> str:
        cleaned = "/" + value.strip("/")
        if cleaned == "/":
            raise ValueError("api_prefix must not be empty")
        return cleaned

    @model_validator(mode="after")
    def _auth_fields(self) -> Upstream:
        if self.auth == "api_key" and not self.api_key_env:
            raise ValueError(f"{self.origin}: auth api_key needs api_key_env")
        if self.auth == "client_credentials":
            missing = [
                name
                for name in ("token_url", "client_id", "client_secret_env")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(
                    f"{self.origin}: auth client_credentials needs {', '.join(missing)}"
                )
        return self

    @property
    def base(self) -> str:
        """``{origin}{api_prefix}``."""
        return f"{self.origin}{self.api_prefix}"


class UpstreamsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upstreams: list[Upstream] = Field(default_factory=list)


def load_upstreams(
    path: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Upstream]:
    """Upstreams keyed by origin. ``path`` defaults to ``TEGEL_UPSTREAMS``; unset means none.

    Every secret variable the file names must be set.
    """
    env = os.environ if environ is None else environ
    value = str(path) if path is not None else env.get(UPSTREAMS_ENV, "").strip()
    if not value:
        return {}
    loaded = yaml.safe_load(Path(value).read_text(encoding="utf-8")) or {}
    found = UpstreamsFile.model_validate(loaded)
    upstreams: dict[str, Upstream] = {}
    for upstream in found.upstreams:
        if upstream.origin in upstreams:
            raise ValueError(f"upstream {upstream.origin} is listed twice in {value}")
        for name in (upstream.api_key_env, upstream.client_secret_env):
            if name and not env.get(name, ""):
                raise ValueError(f"upstream {upstream.origin} needs {name} to be set")
        upstreams[upstream.origin] = upstream
    return upstreams


def upstream_timeout(environ: Mapping[str, str] | None = None) -> float:
    """``TEGEL_UPSTREAM_TIMEOUT`` in seconds, default 10."""
    env = os.environ if environ is None else environ
    value = env.get(TIMEOUT_ENV, "").strip()
    return float(value) if value else DEFAULT_TIMEOUT


def split_remote(url: str, upstreams: Mapping[str, Upstream]) -> tuple[Upstream, str]:
    """Return the upstream and the node path for a ``{origin}{api_prefix}/metadata/{path}`` URL."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc.lower()}"
    upstream = upstreams.get(origin)
    if upstream is None:
        raise ValueError(f"{url}: origin {origin} is not listed in {UPSTREAMS_ENV}")
    if parts.query or parts.fragment:
        raise ValueError(f"{url}: a remote part URL has no query or fragment")
    marker = f"{upstream.api_prefix}/metadata/"
    if not parts.path.startswith(marker):
        raise ValueError(f"{url}: path must start with {marker}")
    remote_path = parts.path[len(marker) :].strip("/")
    segments = remote_path.split("/")
    if not remote_path or any(unquote(seg) in {"", ".", ".."} for seg in segments):
        raise ValueError(f"{url}: the node path is empty or not normalized")
    return upstream, remote_path


class UpstreamAuth(httpx.Auth):
    """Credentials for one upstream. Client-credentials tokens are cached until they expire.

    Only the token response is read; data responses stay streamable.
    """

    def __init__(self, upstream: Upstream, environ: Mapping[str, str] | None = None) -> None:
        self.upstream = upstream
        self._env = os.environ if environ is None else environ
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires = 0.0

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        if self.upstream.auth == "client_credentials" and self._cached() is None:
            response = yield self._token_request()
            response.read()
            self._store(response)
        yield self._sign(request)

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        if self.upstream.auth == "client_credentials" and self._cached() is None:
            response = yield self._token_request()
            await response.aread()
            self._store(response)
        yield self._sign(request)

    def _sign(self, request: httpx.Request) -> httpx.Request:
        upstream = self.upstream
        if upstream.auth == "api_key":
            request.headers["Authorization"] = f"Apikey {self._secret(upstream.api_key_env)}"
        elif upstream.auth == "client_credentials":
            request.headers["Authorization"] = f"Bearer {self._cached()}"
        else:
            request.headers.pop("Authorization", None)
        return request

    def _secret(self, name: str | None) -> str:
        value = self._env.get(name or "", "")
        if not value:
            raise httpx.RequestError(f"upstream {self.upstream.origin}: {name} is not set")
        return value

    def _cached(self) -> str | None:
        with self._lock:
            if self._token is not None and time.monotonic() < self._expires:
                return self._token
        return None

    def _token_request(self) -> httpx.Request:
        upstream = self.upstream
        data = {
            "grant_type": "client_credentials",
            "client_id": upstream.client_id or "",
            "client_secret": self._secret(upstream.client_secret_env),
        }
        if upstream.scope:
            data["scope"] = upstream.scope
        return httpx.Request("POST", upstream.token_url or "", data=data)

    def _store(self, response: httpx.Response) -> str:
        if response.status_code != 200:
            raise httpx.HTTPStatusError(
                f"upstream {self.upstream.origin}: token request returned "
                f"{response.status_code}",
                request=response.request,
                response=response,
            )
        body = response.json()
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise httpx.RequestError(f"upstream {self.upstream.origin}: no access_token")
        lifetime = float(body.get("expires_in") or 60)
        with self._lock:
            self._token = token
            self._expires = time.monotonic() + max(lifetime - 30.0, lifetime / 2)
        return token
