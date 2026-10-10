"""Capabilities handshake."""

from pydantic import BaseModel, ConfigDict, Field

from tegel.core.version import SPEC_VERSION


class InstalledPlugin(BaseModel):
    """One entry point the server loaded."""

    model_config = ConfigDict(extra="ignore")

    group: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class Principal(BaseModel):
    """Caller identity after the auth provider accepts a bearer token."""

    model_config = ConfigDict(extra="ignore")

    subject: str = Field(min_length=1)


class Capabilities(BaseModel):
    """What a server implements, returned by ``GET /capabilities``."""

    model_config = ConfigDict(extra="ignore")

    spec_version: str = SPEC_VERSION
    server_version: str = Field(min_length=1)
    plugins: list[InstalledPlugin] = Field(default_factory=list)
    search_document: str = "ro-crate"
    auth: str = "keycloak"
