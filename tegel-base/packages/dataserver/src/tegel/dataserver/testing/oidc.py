"""A local OpenID discovery document for tests. It is not a Keycloak server."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm


@dataclass
class IssuedToken:
    """Values a test uses to call the reference server."""

    issuer: str
    audience: str
    client_id: str
    client_secret: str
    token: str


class OIDCIssuer:
    """Serve discovery and JWKS on an ephemeral port, and sign one token."""

    def __init__(self, *, lifetime: int = 600) -> None:
        self._lifetime = lifetime
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.issued: IssuedToken | None = None
        self._issuer = ""

    def __enter__(self) -> IssuedToken:
        public = json.loads(RSAAlgorithm.to_jwk(self._key.public_key()))
        public.update({"kid": "test", "use": "sig", "alg": "RS256"})
        holder: dict[str, object] = {"public": public, "issuer": ""}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                issuer = str(holder["issuer"])
                if self.path.endswith("/.well-known/openid-configuration"):
                    body = {
                        "issuer": issuer,
                        "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
                        "authorization_endpoint": f"{issuer}/auth",
                        "token_endpoint": f"{issuer}/token",
                        "end_session_endpoint": f"{issuer}/logout",
                        "device_authorization_endpoint": f"{issuer}/device",
                        "id_token_signing_alg_values_supported": ["RS256"],
                    }
                elif self.path.endswith("/protocol/openid-connect/certs"):
                    body = {"keys": [holder["public"]]}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = self._server.server_address[1]
        issuer = f"http://127.0.0.1:{port}/realms/test"
        holder["issuer"] = issuer
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        holder["audience"] = "tegel"
        self._issuer = issuer
        audience = "tegel"
        token = self.sign()
        self.issued = IssuedToken(
            issuer=issuer,
            audience=audience,
            client_id="tegel",
            client_secret="test-secret",
            token=token,
        )
        return self.issued

    def sign(
        self,
        *,
        subject: str = "user-1",
        scope: str | None = None,
        audience: str = "tegel",
    ) -> str:
        """Sign another token with this issuer's key. ``scope`` is the space-separated claim."""
        now = int(time.time())
        claims: dict[str, object] = {
            "iss": self._issuer,
            "aud": audience,
            "sub": subject,
            "iat": now,
            "exp": now + self._lifetime,
        }
        if scope is not None:
            claims["scope"] = scope
        return jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": "test"})

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
