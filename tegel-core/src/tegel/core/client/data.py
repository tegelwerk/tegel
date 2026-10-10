"""HTTP client for the read-only data server.

The client sends a bearer token. It can be given the token, or a token URL
with a client id and secret for an OAuth2 client-credentials grant. It does
not embed a secret or a default host. There is no write method.
"""

from __future__ import annotations

from typing import Any

import httpx

from tegel.core.contracts.capabilities import Capabilities
from tegel.core.contracts.resources import (
    DataDescriptor,
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


class DataClient:
    """Capabilities, search, browse, get, metadata, and data descriptors."""

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
    ) -> None:
        self.base_url = base_url.rstrip("/")
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

    def open_data(self, resource_id: str) -> DataDescriptor:
        """Return the data descriptor. Array payloads are not downloaded."""
        return DataDescriptor.model_validate(
            self._get_json(f"/resources/{_segment(resource_id)}/data")
        )

    def read_bytes(self, resource_id: str) -> bytes:
        """Download content for a ``bytes`` resource."""
        descriptor = self.open_data(resource_id)
        if descriptor.family is not StructureFamily.bytes:
            raise DataClientError(
                f"read_bytes requires family 'bytes', got {descriptor.family.value}"
            )
        path = f"/resources/{_segment(resource_id)}/data/content"
        response = self._request("GET", path)
        return response.content

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
