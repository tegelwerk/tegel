"""Read API mounted on the Tiled application.

``/data`` returns the parts staging stored, with content URLs filled in per
request. Tegel serves byte parts and the payload with HTTP Range; Tiled serves
array and table parts on its own routes; ``proxy`` parts stream from upstream.

``POST /refresh-catalog`` reopens the catalog after an external merge. It does
not write a record, and no route calls ``insert_record``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import BinaryIO

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from tegel.core.contracts.capabilities import Capabilities
from tegel.core.contracts.resources import (
    DataPart,
    build_search_crate,
    hit_from_metadata,
    metadata_matches,
    resource_uri,
    unknown_filters,
)
from tegel.core.version import SPEC_VERSION
from tegel.dataserver.auth.keycloak import AuthenticationError
from tegel.dataserver.parts import RangeNotSatisfiable, descriptor_for, parse_range
from tegel.dataserver.proxy import proxy_part
from tegel.dataserver.version import __version__

CHUNK = 1 << 16
_NO_URLS = {"parts": {"__all__": {"content_url"}}, "payload": {"content_url"}}


def _chunks(handle: BinaryIO) -> Iterator[bytes]:
    try:
        while block := handle.read(CHUNK):
            yield block
    finally:
        handle.close()


def mount_routes(app: FastAPI) -> None:
    """Add the read routes and the catalog refresh."""

    def principal(request: Request) -> None:
        try:
            request.app.state.tegel_auth.authenticate(request.headers.get("authorization"))
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail="Authentication required") from exc

    def resource_or_404(resource_id: str):
        if not resource_id or "/" in resource_id or "\\" in resource_id:
            raise HTTPException(status_code=404, detail="Resource not found")
        found = app.state.tegel_index.get(resource_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return found

    @app.get("/capabilities")
    def capabilities(request: Request) -> dict:
        principal(request)
        profile = app.state.tegel_profile
        body = Capabilities(
            spec_version=SPEC_VERSION,
            server_version=__version__,
            plugins=app.state.tegel_registry.installed(),
            search_document=app.state.tegel_config.search_document,
            auth=app.state.tegel_auth.name,
            schema_id=profile.schema_id,
            schema_version=profile.schema_version,
            class_name=profile.class_name,
        )
        return body.model_dump(by_alias=True)

    @app.get("/search")
    def search(request: Request) -> Response:
        principal(request)
        profile = app.state.tegel_profile
        filters = {key: value for key, value in request.query_params.items()}
        missing = unknown_filters(filters, profile)
        if missing:
            names = ", ".join(missing)
            raise HTTPException(status_code=400, detail=f"Unknown search field {names}")
        origin = app.state.tegel_config.public_origin
        hits = []
        for resource_id in app.state.tegel_index.keys():
            found = app.state.tegel_index.get(resource_id)
            if found is None or not metadata_matches(found.metadata, filters):
                continue
            hits.append(
                hit_from_metadata(
                    resource_uri(origin, resource_id),
                    found.metadata,
                    profile,
                    fallback_id=resource_id,
                )
            )
        if app.state.tegel_config.search_document != "ro-crate":
            raise HTTPException(status_code=404, detail="Search document is not available")
        document = build_search_crate(hits, profile, origin)
        return Response(content=json.dumps(document), media_type="application/ld+json")

    @app.get("/resources")
    def browse(request: Request) -> dict:
        principal(request)
        resources = []
        for resource_id in app.state.tegel_index.keys():
            found = app.state.tegel_index.get(resource_id)
            if found is None:
                continue
            resources.append(found.model_dump(include={"id", "uri", "type"}))
        return {"resources": resources, "count": len(resources)}

    @app.get("/resources/{resource_id}")
    def get_resource(resource_id: str, request: Request) -> dict:
        principal(request)
        return resource_or_404(resource_id).model_dump(exclude=_NO_URLS)

    @app.get("/resources/{resource_id}/metadata")
    def get_metadata(resource_id: str, request: Request) -> dict:
        principal(request)
        return resource_or_404(resource_id).metadata

    @app.get("/resources/{resource_id}/data")
    def get_data(resource_id: str, request: Request) -> dict:
        principal(request)
        found = resource_or_404(resource_id)
        descriptor = descriptor_for(
            found,
            public_origin=app.state.tegel_config.public_origin,
            upstreams=app.state.tegel_upstreams.upstreams,
        )
        return descriptor.model_dump(mode="json")

    @app.get("/resources/{resource_id}/data/content")
    def get_content(resource_id: str, request: Request) -> Response:
        principal(request)
        found = resource_or_404(resource_id)
        if found.payload is None:
            raise HTTPException(status_code=404, detail="Resource has no payload")
        return serve_bytes(found.payload, request)

    @app.get("/resources/{resource_id}/data/{part_name}/content")
    def get_part_content(resource_id: str, part_name: str, request: Request) -> Response:
        principal(request)
        found = resource_or_404(resource_id)
        part = next((item for item in found.parts if item.name == part_name), None)
        if part is None:
            raise HTTPException(status_code=404, detail="Part not found")
        if part.served_by == "tiled":
            raise HTTPException(status_code=404, detail="Part is served by Tiled; use content_url")
        if part.served_by == "link":
            raise HTTPException(
                status_code=404, detail="Part is served by its upstream; use content_url"
            )
        if part.served_by == "proxy":
            return proxy_part(part, request, app.state.tegel_upstreams)
        return serve_bytes(part, request)

    def serve_bytes(part: DataPart, request: Request) -> Response:
        storage = app.state.tegel_storage
        try:
            size = storage.size(part.asset_uri)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Part content not found") from exc
        try:
            wanted = parse_range(request.headers.get("range"), size)
        except RangeNotSatisfiable:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
            )
        if wanted is None:
            return StreamingResponse(
                _chunks(storage.open_bytes(part.asset_uri)),
                media_type=part.media_type,
                headers={"Content-Length": str(size), "Accept-Ranges": "bytes"},
            )
        body = storage.open_range(part.asset_uri, wanted.start, wanted.end)
        return Response(
            content=body,
            status_code=206,
            media_type=part.media_type,
            headers={
                "Content-Range": f"bytes {wanted.start}-{wanted.end}/{size}",
                "Accept-Ranges": "bytes",
            },
        )

    @app.post("/refresh-catalog")
    async def refresh_catalog(request: Request) -> dict[str, bool]:
        principal(request)
        await reopen_catalog(request)
        return {"refreshed": True}


async def reopen_catalog(request: Request) -> None:
    """Dispose Tiled's pooled catalog connections and open a new pool."""
    from tiled.server.connection_pool import (
        close_database_connection_pool,
        get_database_engine,
    )

    tree = request.app.state.root_tree
    context = getattr(tree, "context", None)
    settings = getattr(context, "database_settings", None)
    if context is None or settings is None:
        raise HTTPException(status_code=409, detail="Catalog is not database-backed")
    await close_database_connection_pool(settings)
    context.engine = get_database_engine(settings)
