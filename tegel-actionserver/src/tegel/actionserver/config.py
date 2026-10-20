"""Configuration for one action server process, read from ``TEGEL_*`` variables."""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tegel.core.spec import readable_storage_from_env

PACKAGE_RESULTS = Path(__file__).parent / "results"


class ActionServerConfig(BaseModel):
    """Everything the standalone server needs. Nothing here names a host or holds a token."""

    model_config = ConfigDict(extra="forbid")

    public_origin: str = Field(min_length=1)
    results_dir: Path
    results_catalog_uri: str = ""
    staging_catalog_uri: str = ""
    runs_db: Path | None = None
    results_profile: Path = PACKAGE_RESULTS / "profile.yaml"
    results_schema_root: Path = PACKAGE_RESULTS
    auth_name: str = "apikey"
    issuer_url: str = ""
    audience: str = ""
    client_id: str = ""
    session_secret: str = Field(default_factory=lambda: secrets.token_hex(32))
    host: str = "127.0.0.1"
    port: int = 8010
    readable_storage: list[Path] = Field(default_factory=list)
    actions_glob: str = "*"
    workers: int = Field(default=2, ge=1)
    resources: dict[str, int] = Field(default_factory=dict)
    token_mode: str = "forward"
    forward_origins: list[str] = Field(default_factory=list)
    input_origins: list[str] | None = None
    exchange_url: str | None = None
    exchange_client_id: str | None = None
    exchange_client_secret: str | None = None
    transfer_warn_seconds: float = Field(default=30.0, ge=0)
    default_throughput: float = Field(default=20e6, gt=0)
    http_timeout: float = Field(default=30.0, gt=0)

    @field_validator("token_mode")
    @classmethod
    def _mode(cls, value: str) -> str:
        if value not in ("forward", "exchange"):
            raise ValueError("TEGEL_TOKEN_EXCHANGE must be 'forward' or 'exchange'")
        return value

    def model_post_init(self, _context: object) -> None:
        self.results_dir = self.results_dir.resolve()
        if not self.results_catalog_uri:
            self.results_catalog_uri = f"sqlite:///{self.results_dir / 'results.db'}"
        if not self.staging_catalog_uri:
            self.staging_catalog_uri = f"sqlite:///{self.results_dir / 'staging.db'}"
        if self.runs_db is None:
            self.runs_db = self.results_dir / "runs.db"
        self.public_origin = self.public_origin.rstrip("/")
        self.forward_origins = [_origin(o) for o in self.forward_origins]
        if self.input_origins is not None:
            self.input_origins = [_origin(o) for o in self.input_origins]

    @property
    def outputs_dir(self) -> Path:
        """Where destage writes output files. The results catalog references them in place."""
        return self.results_dir / "outputs"

    @property
    def landing_dir(self) -> Path:
        """Where staging copies inputs that are not readable in place."""
        return self.results_dir / "landing"

    @property
    def work_dir(self) -> Path:
        """Scratch space; one directory per run, removed when the run ends."""
        return self.results_dir / "work"

    def served_storage(self) -> list[Path]:
        """Directories the results and staging catalogs may reference."""
        return [self.outputs_dir, self.landing_dir, *self.readable_storage]

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ActionServerConfig:
        env = os.environ if environ is None else environ

        def get(name: str, default: str = "") -> str:
            return env.get(name, "").strip() or default

        def required(name: str) -> str:
            value = get(name)
            if not value:
                raise ValueError(f"{name} is required")
            return value

        values: dict[str, object] = {
            "public_origin": required("TEGEL_PUBLIC_ORIGIN"),
            "results_dir": Path(required("TEGEL_RESULTS_DIR")),
            "results_catalog_uri": get("TEGEL_RESULTS_CATALOG_URI"),
            "staging_catalog_uri": get("TEGEL_STAGING_CATALOG_URI"),
            "auth_name": get("TEGEL_AUTH", "apikey"),
            "issuer_url": get("TEGEL_OIDC_ISSUER"),
            "audience": get("TEGEL_OIDC_AUDIENCE"),
            "client_id": get("TEGEL_OIDC_CLIENT_ID"),
            "host": get("TEGEL_HOST", "127.0.0.1"),
            "port": int(get("TEGEL_PORT", "8010")),
            "readable_storage": readable_storage_from_env(env)
            if get("TEGEL_READABLE_STORAGE") or get("TEGEL_RECORDS")
            else [],
            "actions_glob": get("TEGEL_ACTIONS", "*"),
            "workers": int(get("TEGEL_ACTION_WORKERS", "2")),
            "resources": _resources(get("TEGEL_ACTION_RESOURCES")),
            "token_mode": get("TEGEL_TOKEN_EXCHANGE", "forward"),
            "forward_origins": [o for o in get("TEGEL_FORWARD_ORIGINS").split(",") if o.strip()],
            "exchange_url": get("TEGEL_TOKEN_EXCHANGE_URL") or None,
            "exchange_client_id": get("TEGEL_TOKEN_EXCHANGE_CLIENT_ID") or None,
            "exchange_client_secret": get("TEGEL_TOKEN_EXCHANGE_CLIENT_SECRET") or None,
            "transfer_warn_seconds": float(get("TEGEL_TRANSFER_WARN_SECONDS", "30")),
            "default_throughput": float(get("TEGEL_DEFAULT_THROUGHPUT", "20e6")),
        }
        if get("TEGEL_INPUT_ORIGINS"):
            values["input_origins"] = [
                o for o in get("TEGEL_INPUT_ORIGINS").split(",") if o.strip()
            ]
        if get("TEGEL_RUNS_DB"):
            values["runs_db"] = Path(get("TEGEL_RUNS_DB"))
        if get("TEGEL_RESULTS_PROFILE"):
            values["results_profile"] = Path(get("TEGEL_RESULTS_PROFILE"))
            values["results_schema_root"] = Path(required("TEGEL_RESULTS_SCHEMA_ROOT"))
        if get("TEGEL_SESSION_SECRET"):
            values["session_secret"] = get("TEGEL_SESSION_SECRET")
        return cls(**values)


def _resources(text: str) -> dict[str, int]:
    """``gpu=1,catalog-writer=1`` -> slots per named resource. Unlisted resources get one."""
    found: dict[str, int] = {}
    for item in text.split(","):
        if not item.strip():
            continue
        name, _, count = item.partition("=")
        found[name.strip()] = int(count) if count.strip() else 1
    return found


def _origin(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"TEGEL_FORWARD_ORIGINS entry is not an http(s) origin: {url!r}")
    return f"{parts.scheme}://{parts.netloc}"
