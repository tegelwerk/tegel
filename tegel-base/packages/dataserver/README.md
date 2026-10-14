# tegel-dataserver

Reference data server. The contract it implements is [`tegel-core/specs/dataserver.md`](../../../tegel-core/specs/dataserver.md).

`create_app(ServerConfig)` loads the search profile and the LinkML schema that profile names under `TEGEL_SCHEMA_ROOT`, opens the sqlite Tiled catalog with `readable_storage` as Tiled's readable roots, loads every installed entry point and the upstreams file, and builds Tiled's application with one auth provider and anonymous access off. It then mounts the read API:

- `GET /capabilities`, `/search`, `/resources`, `/resources/{id}`, `/resources/{id}/metadata`
- `GET /resources/{id}/data`: the parts and payload staging stored, each with a `content_url` built per request (`tegel.dataserver.parts`). No reader is called.
- `GET /resources/{id}/data/content` (the payload) and `GET /resources/{id}/data/{part}/content` (a Tegel-served part), both with HTTP Range; on a `proxy` part the second streams from its upstream Tiled server (`tegel.dataserver.proxy`)
- `POST /refresh-catalog`, which reopens the catalog's connection pool and writes nothing

Tiled's own `/api/v1`, `/api/graphql`, and `/zarr` routes stay mounted. Array and table parts are read there, through their `content_url`. Provider tokens get read scopes only.

Startup fails when `tiled.graph` is missing, when Tiled is older than 0.2.15, when the catalog has no `entities` table, when a profile field is not a slot of the named class, when no resource type is installed or one names a schema other than the profile's `schema_id`, or when an entry point fails to load.

Every catalog row is `{"record": ..., "tegel": ...}`. `GET /resources/{id}/metadata` returns the record; `type`, `type_version`, `provenance`, `relations`, `payload_uri`, `parts` and `payload` on the resource come from the `tegel` block. `GET /capabilities` also reports the profile's `schema_id`, `schema_version`, and `class`. The index lists only children of the catalog root, so part nodes are never resources. It skips a row of any other shape, including a 0.3 row without `parts`, and logs a warning on `tegel.index`.

The process does not walk directories, extract metadata, copy payloads, or merge catalogs. `tegel.dataserver.catalog.insert_record` writes one catalog node, whose metadata is a row built with `tegel.core.plugins.catalog_metadata`, for tests and ETL code. No route calls it.

## Run

```bash
uv run python -m tegel.dataserver
```

Required: `TEGEL_PUBLIC_ORIGIN`, `TEGEL_CATALOG_URI`, `TEGEL_PROFILE`, `TEGEL_SCHEMA_ROOT`, `TEGEL_OIDC_ISSUER`, `TEGEL_OIDC_AUDIENCE`, `TEGEL_OIDC_CLIENT_ID`, `TEGEL_SESSION_SECRET`.

Optional: `TEGEL_OIDC_CLIENT_SECRET`, `TEGEL_AUTH` (default `keycloak`), `TEGEL_HOST` (default `127.0.0.1`), `TEGEL_PORT` (default `8000`), `TEGEL_ROOT_PATH` (path prefix behind a proxy), `TEGEL_READABLE_STORAGE` (directories Tiled may read assets from; default `TEGEL_RECORDS`), `TEGEL_UPSTREAMS` (upstreams YAML for remote parts) and `TEGEL_UPSTREAM_TIMEOUT` (seconds, default 10).

## Plugins it registers

| Group | Name | Class |
| --- | --- | --- |
| `tegel.auth` | `keycloak` | `tegel.dataserver.auth.keycloak:KeycloakAuth` |
| `tegel.storage` | `filesystem` | `tegel.dataserver.storage.filesystem:FileStorage` |
| `tegel.index` | `tiled` | `tegel.dataserver.index.tiled_catalog:TiledCatalogIndex` |

`KeycloakAuth` verifies RS256 bearer tokens against the issuer's discovery document and JWKS, with the configured issuer and audience. `FileStorage` opens local `file:` URIs: `open_bytes`, `open_range(uri, start, end)` and `size`. `TiledCatalogIndex` reads node keys and metadata from the sqlite catalog.

## Conformance kit

`tegel.dataserver.testing` is also registered as the pytest plugin `tegel`.

- `assert_incarnation(package_root, *, base_url, token, http_client, resource_id, search_field, search_value, positive_fixture, rejected_input, schema_root=None)` checks an incarnation package end to end. It checks the layout, the project file, that the package contains no schema, the profile against its external schema, that the resource type uses the profile's schema, categorical search fields, the extractor on the positive fixture and the rejected input, the stored row built from its package, the reader's `describe` (a valid `DataDescriptor` whose array parts carry a shape hint), and the read API.
- `assert_incarnation_layout(package_root)` and `assert_incarnation_ships_no_schema(package_root)` run single parts of that check.
- `assert_read_only_api(base_url, *, token, http_client, resource_id, search_field, search_value)` checks the HTTP API alone, including `/data`, a one-element slice of a Tiled array part and `Range: bytes=0-0` on a Tegel part, when the resource has them.
- `OIDCIssuer` serves a local discovery document and JWKS on an ephemeral port and signs one token, for tests.
