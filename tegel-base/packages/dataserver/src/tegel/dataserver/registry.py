"""Load entry points and keep the configured auth, index, and storage."""

from __future__ import annotations

from tegel.core.contracts.types import SearchProfile
from tegel.core.plugins import PluginContractError, Registry, readers_in_order
from tegel.core.plugins import schema_mismatch as core_schema_mismatch
from tegel.dataserver.auth.keycloak import KeycloakAuth
from tegel.dataserver.base import AuthProvider, Storage
from tegel.dataserver.config import ServerConfig
from tegel.dataserver.index.tiled_catalog import TiledCatalogIndex

__all__ = [
    "build_auth",
    "build_index",
    "build_storage",
    "load_registry",
    "readers_in_order",
    "schema_mismatch",
]


def load_registry() -> Registry:
    """Discover installed entry points. A broken plugin stops startup."""
    return Registry()


def schema_mismatch(registry: Registry, profile: SearchProfile) -> str | None:
    """Return why the installed resource types do not serve the profile's schema."""
    return core_schema_mismatch(registry.resource_types(), profile)


def build_auth(registry: Registry, config: ServerConfig) -> AuthProvider:
    """Construct the named auth provider. The reference name is ``keycloak``."""
    provider = _named(registry, "tegel.auth", config.auth_name)
    if provider is KeycloakAuth:
        return KeycloakAuth(
            issuer_url=config.issuer_url,
            audience=config.audience,
            client_id=config.client_id,
        )
    return _construct(provider, f"tegel.auth:{config.auth_name}")


def build_storage(registry: Registry, config: ServerConfig) -> Storage:
    """Construct the named storage backend."""
    provider = _named(registry, "tegel.storage", config.storage_name)
    return _construct(provider, f"tegel.storage:{config.storage_name}")


def build_index(
    registry: Registry,
    config: ServerConfig,
    profile: SearchProfile,
) -> TiledCatalogIndex:
    """Construct the named index. The reference index reads the Tiled catalog."""
    provider = _named(registry, "tegel.index", config.index_name)
    if provider is TiledCatalogIndex:
        return TiledCatalogIndex(
            catalog_uri=config.catalog_uri,
            public_origin=config.public_origin,
            profile=profile,
        )
    return _construct(provider, f"tegel.index:{config.index_name}")


def _named(registry: Registry, group: str, name: str) -> type:
    found = registry.groups[group].get(name)
    if found is None:
        raise PluginContractError(f"{group}:{name} is not installed")
    if not isinstance(found, type):
        raise PluginContractError(f"{group}:{name}: must be a class")
    return found


def _construct(cls: type, origin: str) -> object:
    try:
        return cls()
    except Exception as exc:
        raise PluginContractError(
            f"{origin}: must construct with no arguments ({exc})"
        ) from exc
