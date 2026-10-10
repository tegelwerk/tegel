"""Checks an incarnation server can run against the read API."""

from __future__ import annotations

import httpx

from tegel.core.client.data import DataClient, DataClientError
from tegel.core.contracts.resources import StructureFamily


def assert_read_only_api(
    base_url: str,
    *,
    token: str,
    http_client: httpx.Client,
    resource_id: str,
    search_field: str,
    search_value: str,
) -> None:
    """Search, fetch, and open one resource. Reject a missing token and a write."""
    anonymous = DataClient(base_url, client=http_client)
    try:
        anonymous.capabilities()
    except DataClientError as exc:
        if exc.status_code != 401:
            raise
    else:
        raise AssertionError("capabilities without a token must be rejected")

    with DataClient(base_url, token=token, client=http_client) as client:
        found = client.capabilities()
        if found.auth != "keycloak":
            raise AssertionError(f"auth is {found.auth}")
        if found.search_document != "ro-crate":
            raise AssertionError(f"search_document is {found.search_document}")
        result = client.search({search_field: search_value})
        if result.document != "ro-crate" or result.count < 1:
            raise AssertionError("search did not return an RO-Crate hit")
        try:
            client.search({"not-a-field": "x"})
        except DataClientError as exc:
            if exc.status_code != 400 or "not-a-field" not in exc.message:
                raise
        else:
            raise AssertionError("an unknown search field must be rejected")
        resource = client.get(resource_id)
        if resource.id != resource_id:
            raise AssertionError("get returned a different id")
        metadata = client.metadata(resource_id)
        if not isinstance(metadata, dict):
            raise AssertionError("metadata must be an object")
        descriptor = client.open_data(resource_id)
        if descriptor.family is StructureFamily.array and not descriptor.shape:
            raise AssertionError("an array descriptor must include shape")

    denied = http_client.post(f"{base_url.rstrip('/')}/resources")
    if denied.status_code != 405:
        raise AssertionError(f"POST /resources returned {denied.status_code}")
