"""Build a Tiled server modified to the data-server specification."""

from __future__ import annotations

from fastapi import FastAPI

from tegel.dataserver.auth.adapter import install_bearer_check, reset_provider, use_provider
from tegel.dataserver.catalog import open_catalog
from tegel.dataserver.config import ServerConfig
from tegel.dataserver.registry import (
    build_auth,
    build_index,
    build_storage,
    load_registry,
    reader_for,
    readers_in_order,
)
from tegel.dataserver.routes import mount_routes
from tegel.dataserver.spec import load_spec


def create_app(config: ServerConfig) -> FastAPI:
    """Read the incarnation spec and launch Tiled in the form that spec requires.

    The returned application is Tiled's server, including the entity/link graph
    when the catalog is catalog-backed, with the read API mounted on it.
    Anonymous access is off. The process does not run ETL.
    """
    profile, _schema = load_spec(config.profile_path, config.schema_root)
    tree = open_catalog(config.catalog_uri)
    registry = load_registry()
    auth = build_auth(registry, config)
    index = build_index(registry, config, profile)
    storage = build_storage(registry, config)
    readers = readers_in_order(registry)

    from tiled.config import Authentication, AuthenticationProviderSpec
    from tiled.server.app import build_app

    holder = use_provider(auth)
    try:
        tiled_auth = Authentication(
            allow_anonymous_access=False,
            secret_keys=[config.session_secret],
            providers=[
                AuthenticationProviderSpec(
                    provider=auth.name,
                    authenticator="tegel.dataserver.auth.adapter:TiledAuthAdapter",
                )
            ],
        )
        app = build_app(tree, authentication=tiled_auth)
    finally:
        reset_provider(holder)
    install_bearer_check(app, auth)
    app.state.provider = auth.name
    app.state.tegel_config = config
    app.state.tegel_profile = profile
    app.state.tegel_registry = registry
    app.state.tegel_auth = auth
    app.state.tegel_tiled_auth = tiled_auth.authenticators[auth.name]
    app.state.tegel_index = index
    app.state.tegel_storage = storage
    app.state.tegel_readers = readers
    app.state.tegel_reader_for = lambda resource: reader_for(readers, resource)
    app.state.tegel_catalog = tree
    mount_routes(app)
    return app
