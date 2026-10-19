"""Keycloak tokens carry OAuth scopes into the principal."""

from tegel.dataserver.auth.keycloak import KeycloakAuth
from tegel.dataserver.testing.oidc import OIDCIssuer


def test_scope_claim_becomes_principal_scopes() -> None:
    issuer = OIDCIssuer()
    with issuer as issued:
        auth = KeycloakAuth(issuer_url=issued.issuer, audience=issued.audience,
                            client_id=issued.client_id)
        assert auth.authenticate(f"Bearer {issued.token}").scopes == []
        token = issuer.sign(subject="ana", scope="openid actions:run actions:read")
        principal = auth.authenticate(f"Bearer {token}")
        assert principal.subject == "ana"
        assert principal.scopes == ["actions:read", "actions:run", "openid"]
