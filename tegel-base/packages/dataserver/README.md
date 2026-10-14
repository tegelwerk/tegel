# tegel-dataserver

Reference data server. The contract it implements is [`tegel-core/specs/dataserver.md`](../../../tegel-core/specs/dataserver.md).

`create_app(ServerConfig)` loads the search profile and the LinkML schema that profile names under `TEGEL_SCHEMA_ROOT`, opens the sqlite Tiled catalog, loads every installed entry point, and builds Tiled's application with one auth provider and anonymous access off. It then mounts the read API:

- `GET /capabilities`, `/search`, `/resources`, `/resources/{id}`, `/resources/{id}/metadata`, `/resources/{id}/data`, `/resources/{id}/data/content`
- `POST /refresh-catalog`, which reopens the catalog's connection pool and writes nothing

Tiled's own `/api/v1`, `/api/graphql`, and `/zarr` routes stay mounted. Provider tokens get read scopes only.

Startup fails when `tiled.graph` is missing, when Tiled is older than 0.2.15, when the catalog has no `entities` table, when a profile field is not a slot of the named class, or when an entry point fails to load.

The process does not walk directories, extract metadata, copy payloads, or merge catalogs. `tegel.dataserver.catalog.insert_record` writes one catalog node for tests and ETL code. No route calls it.

## Run

```bash
uv run python -m tegel.dataserver
```

Required: `TEGEL_PUBLIC_ORIGIN`, `TEGEL_CATALOG_URI`, `TEGEL_PROFILE`, `TEGEL_SCHEMA_ROOT`, `TEGEL_OIDC_ISSUER`, `TEGEL_OIDC_AUDIENCE`, `TEGEL_OIDC_CLIENT_ID`, `TEGEL_SESSION_SECRET`.

Optional: `TEGEL_OIDC_CLIENT_SECRET`, `TEGEL_AUTH` (default `keycloak`), `TEGEL_HOST` (default `127.0.0.1`), `TEGEL_PORT` (default `8000`), `TEGEL_ROOT_PATH` (path prefix behind a proxy).

## Plugins it registers

| Group | Name | Class |
| --- | --- | --- |
| `tegel.auth` | `keycloak` | `tegel.dataserver.auth.keycloak:KeycloakAuth` |
| `tegel.storage` | `filesystem` | `tegel.dataserver.storage.filesystem:FileStorage` |
| `tegel.index` | `tiled` | `tegel.dataserver.index.tiled_catalog:TiledCatalogIndex` |

`KeycloakAuth` verifies RS256 bearer tokens against the issuer's discovery document and JWKS, with the configured issuer and audience. `FileStorage` opens local `file:` URIs. `TiledCatalogIndex` reads node keys and metadata from the sqlite catalog.

## Conformance kit

`tegel.dataserver.testing` is also registered as the pytest plugin `tegel`.

- `assert_incarnation(package_root, *, base_url, token, http_client, resource_id, search_field, search_value, positive_fixture, rejected_input, schema_root=None)` checks an incarnation package end to end. It checks the layout, the project file, that the package contains no schema, the profile against its external schema, categorical search fields, the extractor on the positive fixture and the rejected input, the reader's `describe`, and the read API.
- `assert_incarnation_layout(package_root)` and `assert_incarnation_ships_no_schema(package_root)` run single parts of that check.
- `assert_read_only_api(base_url, *, token, http_client, resource_id, search_field, search_value)` checks the HTTP API alone.
- `OIDCIssuer` serves a local discovery document and JWKS on an ephemeral port and signs one token, for tests.
