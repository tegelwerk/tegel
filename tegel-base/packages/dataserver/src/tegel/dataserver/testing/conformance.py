"""Checks an incarnation server can run against the read API."""

from __future__ import annotations

import httpx

from tegel.core.client.data import DataClient, DataClientError
from tegel.core.contracts.resources import (
    ENVELOPE_KEY,
    RECORD_KEY,
    DataDescriptor,
    StructureFamily,
)


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
        if metadata != resource.metadata or ENVELOPE_KEY in metadata or RECORD_KEY in metadata:
            raise AssertionError("metadata must be the record block")
        if resource.provenance is None or resource.type is None:
            raise AssertionError("get must project type and provenance from the tegel block")
        descriptor = client.data(resource_id)
        if not isinstance(descriptor, DataDescriptor):
            raise AssertionError("/data must return a descriptor")
        for part in descriptor.parts:
            if not part.content_url or part.served_by is None:
                raise AssertionError(f"part {part.name} must have served_by and content_url")
        _check_parts(client, http_client, token, resource_id, descriptor)

    denied = http_client.post(f"{base_url.rstrip('/')}/resources")
    if denied.status_code != 405:
        raise AssertionError(f"POST /resources returned {denied.status_code}")


def _check_parts(
    client: DataClient,
    http_client: httpx.Client,
    token: str,
    resource_id: str,
    descriptor: DataDescriptor,
) -> None:
    """One Tiled array part answers a one-element slice; one Tegel part answers a range."""
    array = next(
        (
            part
            for part in descriptor.parts
            if part.served_by == "tiled" and part.family is StructureFamily.array
        ),
        None,
    )
    if array is not None:
        index = ",".join("0" for _ in array.shape or [0])
        try:
            client.read_array(resource_id, array, slice=index)
        except DataClientError as exc:
            raise AssertionError(f"slicing array part {array.name} failed: {exc}") from exc
    served = [part for part in descriptor.parts if part.served_by == "tegel"]
    if descriptor.payload is not None:
        served.append(descriptor.payload)
    tegel = next((part for part in served if part.size != 0), None)
    if tegel is None or tegel.content_url is None:
        return
    response = http_client.get(
        tegel.content_url,
        headers={"Authorization": f"Bearer {token}", "Range": "bytes=0-0"},
    )
    if response.status_code != 206 or len(response.content) != 1:
        raise AssertionError(
            f"Range bytes=0-0 on part {tegel.name} returned {response.status_code}"
        )
