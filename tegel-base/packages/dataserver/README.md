# tegel-dataserver

Reference data server. It reads an incarnation's entry points, LinkML schema, and search profile, then launches Tiled with anonymous access off and the read API from `tegel-core` mounted on it.

The Tiled release must include the entity/link graph (`tiled.graph`). The dependency floor is `tiled[server]>=0.2.15`. A newer release stays in range only while that graph is still installed.

This process does not walk directories, extract metadata, or copy payloads. A downstream ETL application writes the catalog. Keycloak is the default auth provider. A deployment selects another installed `tegel.auth` entry point with `TEGEL_AUTH`. That provider is the only authenticator given to Tiled. Issuer URL, audience, client id, and secrets come from configuration.

The development bind address is `127.0.0.1`. `TEGEL_PUBLIC_ORIGIN` has no default.

```bash
uv run python -m tegel.dataserver
```
