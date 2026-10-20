"""Self-hosted namespace service (§21)."""
from __future__ import annotations
import json
import uuid
from dataclasses import dataclass
from datetime import timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import urlparse
from tegel.register.namespaces.base import (
    NamespaceRequestContext, NamespaceResolution, NamespaceResolver, Principal,
)

class Authenticator(Protocol):
    def authenticate(self, request_headers: dict[str, str]) -> Principal | None: ...

class NullAuthenticator:
    def __init__(self, principal: Principal | None = None) -> None:
        self.principal = principal or Principal(id="local")
    def authenticate(self, request_headers: dict[str, str]) -> Principal | None:
        return self.principal

class BearerTokenAuthenticator:
    def __init__(self, tokens: dict[str, Principal]) -> None:
        self.tokens = tokens
    def authenticate(self, request_headers: dict[str, str]) -> Principal | None:
        auth = request_headers.get("Authorization") or request_headers.get("authorization")
        if not auth or not auth.lower().startswith("bearer "):
            return None
        return self.tokens.get(auth.split(" ", 1)[1].strip())

class MTLSAuthenticator:
    def __init__(self, *, require_client_cert: bool = True) -> None:
        self.require_client_cert = require_client_cert
    def authenticate(self, request_headers: dict[str, str]) -> Principal | None:
        cn = request_headers.get("X-Client-CN")
        if not cn:
            return None if self.require_client_cert else Principal(id="anonymous")
        return Principal(id=cn)

@dataclass
class ServerConfig:
    namespace: str
    resolver: NamespaceResolver
    authenticator: Authenticator
    host: str = "127.0.0.1"
    port: int = 8765
    include_detail: bool = False
    secrets: Any = None

def _serialize(resolution: NamespaceResolution, *, include_detail: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"status": resolution.status.value}
    if resolution.uri is not None:
        out["uri"] = resolution.uri
    if resolution.expires_at is not None:
        out["expires_at"] = resolution.expires_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if resolution.retry_after is not None:
        out["retry_after"] = int(resolution.retry_after.total_seconds())
    if resolution.etag is not None:
        out["etag"] = resolution.etag
    if include_detail and resolution.detail is not None:
        out["detail"] = resolution.detail
    return out

def make_handler(config: ServerConfig) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return
        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path.rstrip("/") != "/resolve":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            identifier = body.get("identifier")
            if not isinstance(identifier, str):
                self._json(400, {"error": "identifier required"})
                return
            headers = {k: v for k, v in self.headers.items()}
            principal = config.authenticator.authenticate(headers)
            if principal is None and not isinstance(config.authenticator, NullAuthenticator):
                self._json(401, {"error": "unauthorized"})
                return
            req = NamespaceRequestContext(
                principal=principal, secrets=config.secrets, request_id=str(uuid.uuid4())
            )
            result = config.resolver.resolve_namespace(identifier, req)
            self._json(200, _serialize(result, include_detail=config.include_detail))
        def _json(self, code: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return Handler

def serve(config: ServerConfig, *, blocking: bool = True) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((config.host, config.port), make_handler(config))
    if blocking:
        server.serve_forever()
    return server

def load_object(dotted: str) -> Any:
    module_name, _, attr = dotted.partition(":")
    if not module_name or not attr:
        raise ValueError(f"expected 'module.path:attribute', got {dotted!r}")
    import importlib
    return getattr(importlib.import_module(module_name), attr)
