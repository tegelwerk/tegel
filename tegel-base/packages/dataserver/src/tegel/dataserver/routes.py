"""Read API mounted on the Tiled application.

``POST /refresh-catalog`` reopens the catalog after an external merge. It does
not write a record, and no route calls ``insert_record``.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from tegel.core.contracts.capabilities import Capabilities
from tegel.core.contracts.resources import (
    StructureFamily,
    build_search_crate,
    hit_from_metadata,
    metadata_matches,
    resource_uri,
    unknown_filters,
)
from tegel.core.plugins import PluginContractError
from tegel.core.version import SPEC_VERSION
from tegel.dataserver.auth.keycloak import AuthenticationError
from tegel.dataserver.version import __version__


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
        return resource_or_404(resource_id).model_dump()

    @app.get("/resources/{resource_id}/metadata")
    def get_metadata(resource_id: str, request: Request) -> dict:
        principal(request)
        return resource_or_404(resource_id).metadata

    @app.get("/resources/{resource_id}/data")
    def get_data(resource_id: str, request: Request) -> dict:
        principal(request)
        found = resource_or_404(resource_id)
        try:
            reader = app.state.tegel_reader_for(found)
            descriptor = reader.describe(found)
        except PluginContractError as exc:
            raise HTTPException(status_code=404, detail="No reader validated the resource") from exc
        return descriptor.model_dump()

    @app.get("/resources/{resource_id}/data/content")
    def get_content(resource_id: str, request: Request) -> Response:
        principal(request)
        found = resource_or_404(resource_id)
        try:
            reader = app.state.tegel_reader_for(found)
            descriptor = reader.describe(found)
        except PluginContractError as exc:
            raise HTTPException(status_code=404, detail="No reader validated the resource") from exc
        if descriptor.family is not StructureFamily.bytes:
            raise HTTPException(status_code=404, detail="Content is only available for bytes")
        uri = found.payload_uri
        if not uri:
            raise HTTPException(status_code=404, detail="Resource not found")
        handle = app.state.tegel_storage.open_bytes(uri)
        try:
            payload = handle.read()
        finally:
            handle.close()
        return Response(content=payload, media_type=descriptor.media_type)

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
