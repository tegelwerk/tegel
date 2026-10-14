"""HTTP client for the read-only data server.

The client sends a bearer token. It can be given the token, or a token URL
with a client id and secret for an OAuth2 client-credentials grant. It does
not embed a secret or a default host. There is no write method.

Data parts are read through their ``content_url`` with Tiled's own query
parameters. The token goes only to the client's own origin; a ``link`` part on
another origin uses ``upstream_auth`` for that origin, or nothing. The Tiled
Python client works against the same ``content_url``s for richer use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from tegel.core.contracts.capabilities import Capabilities
from tegel.core.contracts.resources import (
    DataDescriptor,
    DataPart,
    Resource,
    ResourcePage,
    SearchResult,
    StructureFamily,
    parse_search_body,
)
from tegel.core.version import SPEC_VERSION


class DataClientError(Exception):
    """The data server rejected a call or returned a body this client cannot parse."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ArrayBytes:
    """Raw array bytes, with ``shape`` and ``data_type`` taken from the part (not the slice)."""

    data: bytes
    shape: list[int] | None
    data_type: str | None
    part: DataPart


class DataClient:
    """Capabilities, search, browse, get, metadata, and data parts."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        token_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        upstream_auth: dict[str, httpx.Auth] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._origin = _origin(self.base_url)
        self._upstream_auth = {
            _origin(origin): auth for origin, auth in (upstream_auth or {}).items()
        }
        self._token = token
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> DataClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def capabilities(self) -> Capabilities:
        body = self._get_json("/capabilities")
        found = Capabilities.model_validate(body)
        server_major = _major(found.spec_version)
        client_major = _major(SPEC_VERSION)
        if server_major != client_major:
            raise DataClientError(
                f"spec_version {found.spec_version} is incompatible with {SPEC_VERSION}"
            )
        return found

    def search(self, filters: dict[str, str] | None = None) -> SearchResult:
        response = self._request("GET", "/search", params=filters or {})
        content_type = response.headers.get("content-type", "")
        try:
            return parse_search_body(content_type, response.json())
        except (ValueError, httpx.DecodingError) as exc:
            raise DataClientError(f"Could not parse search response: {exc}") from exc

    def browse(self) -> ResourcePage:
        return ResourcePage.model_validate(self._get_json("/resources"))

    def get(self, resource_id: str) -> Resource:
        return Resource.model_validate(self._get_json(f"/resources/{_segment(resource_id)}"))

    def metadata(self, resource_id: str) -> dict[str, Any]:
        body = self._get_json(f"/resources/{_segment(resource_id)}/metadata")
        if not isinstance(body, dict):
            raise DataClientError("Metadata response must be a JSON object")
        return body

    def data(self, resource_id: str) -> DataDescriptor:
        """Return the parts and payload, each with its ``content_url``. Nothing is downloaded."""
        return DataDescriptor.model_validate(
            self._get_json(f"/resources/{_segment(resource_id)}/data")
        )

    def parts(self, resource_id: str) -> list[DataPart]:
        return self.data(resource_id).parts

    def read_array(
        self,
        resource_id: str,
        part: str | DataPart,
        slice: str | None = None,
    ) -> ArrayBytes:
        """Raw bytes of an array part, optionally a Tiled ``slice`` such as ``0:10,::2``."""
        found = self._part(resource_id, part)
        if found.family is not StructureFamily.array:
            raise DataClientError(f"part {found.name} is {found.family.value}, not array")
        params = {"format": "application/octet-stream"}
        if slice is not None:
            params["slice"] = slice
        response = self._fetch(found, params=params)
        return ArrayBytes(response.content, found.shape, found.data_type, found)

    def read_table(
        self,
        resource_id: str,
        part: str | DataPart,
        columns: list[str] | None = None,
        partition: int | None = None,
        format: str = "text/csv",
    ) -> bytes:
        """A table part, or some ``columns`` of it, or one ``partition``, in ``format``."""
        found = self._part(resource_id, part)
        if found.family is not StructureFamily.table:
            raise DataClientError(f"part {found.name} is {found.family.value}, not table")
        params: list[tuple[str, str]] = [("format", format)]
        params.extend(("column", name) for name in columns or [])
        url = self._url(found)
        if partition is not None:
            params.append(("partition", str(partition)))
            url = url.replace("/table/full/", "/table/partition/", 1)
        return self._fetch(found, params=params, url=url).content

    def read_bytes(
        self,
        resource_id: str,
        part: str | DataPart | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> bytes:
        """Bytes of a part, or of the payload when ``part`` is ``None``.

        ``start`` and ``end`` are inclusive offsets sent as an HTTP Range.
        """
        if part is None:
            payload = self.data(resource_id).payload
            if payload is None:
                raise DataClientError("Resource has no payload", status_code=404)
            found = payload
        else:
            found = self._part(resource_id, part)
        headers: dict[str, str] = {}
        if start is not None or end is not None:
            first = "" if start is None else str(start)
            last = "" if end is None else str(end)
            headers["Range"] = f"bytes={first}-{last}"
        return self._fetch(found, headers=headers).content

    def _part(self, resource_id: str, part: str | DataPart) -> DataPart:
        if isinstance(part, DataPart) and part.content_url:
            return part
        name = part.name if isinstance(part, DataPart) else part
        for found in self.parts(resource_id):
            if found.name == name:
                return found
        raise DataClientError(f"part {name} not found", status_code=404)

    def _url(self, part: DataPart) -> str:
        if not part.content_url:
            raise DataClientError(f"part {part.name} has no content_url")
        return part.content_url

    def _fetch(
        self,
        part: DataPart,
        *,
        params: Any = None,
        headers: dict[str, str] | None = None,
        url: str | None = None,
    ) -> httpx.Response:
        target = url or self._url(part)
        sent = dict(headers or {})
        auth: httpx.Auth | None = None
        if _origin(target) == self._origin:
            sent.update(self._headers())
        else:
            auth = self._upstream_auth.get(_origin(target), _NO_AUTH)
        response = self._client.request("GET", target, params=params, headers=sent, auth=auth)
        if response.status_code >= 400:
            raise DataClientError(_detail(response), status_code=response.status_code)
        return response

    def _bearer(self) -> str | None:
        if self._token:
            return self._token
        if not (self._token_url and self._client_id and self._client_secret):
            return None
        response = self._client.post(
            self._token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        if response.status_code >= 400:
            raise DataClientError(
                "Token request failed",
                status_code=response.status_code,
            )
        payload = response.json()
        access = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(access, str) or not access:
            raise DataClientError("Token response did not include access_token")
        self._token = access
        return access

    def _headers(self) -> dict[str, str]:
        token = self._bearer()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        response = self._client.request(
            method,
            f"{self.base_url}{path}",
            params=params,
            headers=self._headers(),
        )
        if response.status_code >= 400:
            detail = _detail(response)
            raise DataClientError(detail, status_code=response.status_code)
        return response

    def _get_json(self, path: str) -> Any:
        response = self._request("GET", path)
        try:
            return response.json()
        except httpx.DecodingError as exc:
            raise DataClientError(f"Response was not JSON: {exc}") from exc


class _NoAuth(httpx.Auth):
    """Send nothing, even if the shared ``httpx.Client`` has its own auth."""

    def auth_flow(self, request: httpx.Request):
        request.headers.pop("Authorization", None)
        yield request


_NO_AUTH = _NoAuth()


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.lower()}"


def _segment(resource_id: str) -> str:
    if not resource_id or "/" in resource_id or "\\" in resource_id:
        raise DataClientError("resource id must be a single path segment")
    return resource_id


def _major(version: str) -> str:
    return version.split(".", 1)[0]


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except httpx.DecodingError:
        return f"HTTP {response.status_code}"
    if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
        return payload["detail"]
    return f"HTTP {response.status_code}"
