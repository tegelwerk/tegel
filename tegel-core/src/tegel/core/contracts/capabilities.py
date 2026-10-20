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
    scopes: list[str] = Field(default_factory=list)


class Capabilities(BaseModel):
    """What a server implements, returned by ``GET /capabilities``."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    spec_version: str = SPEC_VERSION
    server_version: str = Field(min_length=1)
    plugins: list[InstalledPlugin] = Field(default_factory=list)
    search_document: str = "ro-crate"
    auth: str = "keycloak"
    schema_id: str | None = None
    schema_version: str | None = None
    class_name: str | None = Field(default=None, alias="class")
    services: list[str] = Field(default_factory=lambda: ["read"])
