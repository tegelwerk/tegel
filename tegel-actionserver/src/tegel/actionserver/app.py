"""Build the action server: Tiled on the results catalog, the data server read API,
and the action routes, behind one auth provider.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI

from tegel.actionserver.catalogs import Catalogs
from tegel.actionserver.config import ActionServerConfig
from tegel.actionserver.destage import RUN_TYPE, Destager
from tegel.actionserver.executor import Executor
from tegel.actionserver.loading import ActionTable, load_actions
from tegel.actionserver.routes import mount_action_routes
from tegel.actionserver.runs import RunStore
from tegel.actionserver.service import ActionService
from tegel.actionserver.staging import Stager
from tegel.actionserver.tokens import TokenBroker
from tegel.core.plugins import PluginContractError, Registry
from tegel.core.spec import load_spec


def build_auth(config: ActionServerConfig, registry: Registry) -> Any:
    """The configured ``tegel.auth`` provider. ``keycloak`` takes the OIDC settings."""
    provider = registry.groups["tegel.auth"].get(config.auth_name)
    if provider is None:
        raise PluginContractError(f"tegel.auth:{config.auth_name} is not installed")
    from tegel.dataserver.auth.keycloak import KeycloakAuth

    if provider is KeycloakAuth:
        return KeycloakAuth(issuer_url=config.issuer_url, audience=config.audience,
                            client_id=config.client_id)
    return provider()


def create_app(
    config: ActionServerConfig,
    *,
    actions: ActionTable | None = None,
    auth: Any = None,
    bind_context: Any = None,
    http_client: Any = None,
) -> FastAPI:
    """The standalone action server. ``actions``, ``auth`` and the clients are for tests."""
    from tiled.config import Authentication, AuthenticationProviderSpec
    from tiled.server.app import build_app

    from tegel.actionserver import __version__
    from tegel.dataserver.auth.adapter import install_bearer_check, reset_provider, use_provider
    from tegel.dataserver.config import ServerConfig
    from tegel.dataserver.index.tiled_catalog import TiledCatalogIndex
    from tegel.dataserver.proxy import UpstreamClients
    from tegel.dataserver.routes import mount_routes
    from tegel.dataserver.storage.filesystem import FileStorage

    profile, _schema = load_spec(config.results_profile, config.results_schema_root)
    if profile.schema_id != RUN_TYPE.metadata_schema:
        raise RuntimeError(
            f"results profile names {profile.schema_id}; runs are {RUN_TYPE.metadata_schema}"
        )
    installed = Registry()
    auth = auth if auth is not None else build_auth(config, installed)
    actions = actions if actions is not None else load_actions(config.actions_glob)
    registry = Registry(groups={
        "tegel.resource_types": {"tegel-run": RUN_TYPE},
        "tegel.auth": {auth.name: type(auth)},
        "tegel.storage": {"filesystem": FileStorage},
        "tegel.index": {"tiled": TiledCatalogIndex},
    })

    catalogs = Catalogs(config)
    store = RunStore(config.runs_db)  # type: ignore[arg-type]
    service = ActionService(
        actions=actions,
        store=store,
        stager=Stager(config, catalogs, store, client=http_client, bind_context=bind_context),
        destager=Destager(config, catalogs, profile, __version__),
        broker=TokenBroker(config, client=http_client, provider_name=auth.name),
        executor=Executor(config.workers, config.resources),
        transfer_warn_seconds=config.transfer_warn_seconds,
    )

    holder = use_provider(auth)
    try:
        tiled_auth = Authentication(
            allow_anonymous_access=False,
            secret_keys=[config.session_secret],
            providers=[AuthenticationProviderSpec(
                provider=auth.name,
                authenticator="tegel.dataserver.auth.adapter:TiledAuthAdapter",
            )],
        )
        # Tiled keeps login state in one in-memory SQLite shared by every app in the process
        # unless told otherwise; give this app its own.
        from tiled.config import Database

        authdb = f"sqlite:///file:authdb-{uuid.uuid4().hex}?mode=memory&cache=shared&uri=true"
        app = build_app(catalogs.results_tree, authentication=tiled_auth,
                        server_settings={"database": Database(uri=authdb)})
    finally:
        reset_provider(holder)
    install_bearer_check(app, auth)

    # The read routes expect the data server's state.
    app.state.provider = auth.name
    app.state.tegel_config = ServerConfig(
        public_origin=config.public_origin,
        catalog_uri=config.results_catalog_uri,
        profile_path=config.results_profile,
        schema_root=config.results_schema_root,
        issuer_url=config.issuer_url or "-",
        audience=config.audience or "-",
        client_id=config.client_id or "-",
        client_secret="",
        session_secret=config.session_secret,
        auth_name=auth.name,
        readable_storage=config.served_storage(),
    )
    app.state.tegel_profile = profile
    app.state.tegel_registry = registry
    app.state.tegel_auth = auth
    app.state.tegel_index = TiledCatalogIndex(
        catalog_uri=config.results_catalog_uri, public_origin=config.public_origin,
        profile=profile,
    )
    app.state.tegel_storage = FileStorage()
    app.state.tegel_upstreams = UpstreamClients({}, timeout=config.http_timeout)
    app.state.tegel_catalog = catalogs.results_tree
    app.state.tegel_actions = service
    app.state.tegel_action_config = config
    app.state.tegel_server_version = __version__

    mount_action_routes(app)  # first, so /capabilities and /refresh-catalog are ours
    mount_routes(app)
    return app
