"""Site values the reference process reads. Nothing here names a host."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from tegel.core.spec import readable_storage_from_env
from tegel.core.upstreams import DEFAULT_TIMEOUT, UPSTREAMS_ENV, upstream_timeout


class ServerConfig(BaseModel):
    """Configuration for one launch of the reference data server.

    Issuer, catalog, origin, and secrets come from the caller. There is no
    default host and no embedded credential.
    """

    model_config = ConfigDict(extra="forbid")

    public_origin: str = Field(min_length=1)
    catalog_uri: str = Field(min_length=1)
    profile_path: Path
    schema_root: Path
    issuer_url: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    client_secret: str
    session_secret: str = Field(min_length=1)
    host: str = "127.0.0.1"
    port: int = 8000
    auth_name: str = "keycloak"
    index_name: str = "tiled"
    storage_name: str = "filesystem"
    search_document: str = "ro-crate"
    readable_storage: list[Path] = Field(default_factory=list)
    upstreams_path: Path | None = None
    upstream_timeout: float = Field(default=DEFAULT_TIMEOUT, gt=0)

    @classmethod
    def from_env(cls) -> ServerConfig:
        """Build a config from ``TEGEL_*`` environment variables."""

        def required(name: str) -> str:
            value = os.environ.get(name, "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            return value

        port = os.environ.get("TEGEL_PORT", "").strip()
        upstreams = os.environ.get(UPSTREAMS_ENV, "").strip()
        return cls(
            public_origin=required("TEGEL_PUBLIC_ORIGIN"),
            catalog_uri=required("TEGEL_CATALOG_URI"),
            profile_path=Path(required("TEGEL_PROFILE")),
            schema_root=Path(required("TEGEL_SCHEMA_ROOT")),
            issuer_url=required("TEGEL_OIDC_ISSUER"),
            audience=required("TEGEL_OIDC_AUDIENCE"),
            client_id=required("TEGEL_OIDC_CLIENT_ID"),
            client_secret=os.environ.get("TEGEL_OIDC_CLIENT_SECRET", ""),
            session_secret=required("TEGEL_SESSION_SECRET"),
            host=os.environ.get("TEGEL_HOST", "").strip() or "127.0.0.1",
            port=int(port) if port else 8000,
            auth_name=os.environ.get("TEGEL_AUTH", "").strip() or "keycloak",
            readable_storage=readable_storage_from_env(),
            upstreams_path=Path(upstreams) if upstreams else None,
            upstream_timeout=upstream_timeout(),
        )
