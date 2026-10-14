# Data server

The data server reads a catalog that an ETL pipeline has already written and serves it over HTTP. This document is the contract for that read API, the resource model, and the boundaries around ETL, storage, and authentication.

Decisions this contract rests on:

- The HTTP API is read-only. Read-only means no route creates, changes, or deletes a catalog record or a file.
- Filling the catalog, including whether payload bytes are copied, is a downstream ETL application. This specification does not require copying and does not forbid it.
- The reference search document is an RO-Crate. Another specification may define a different document.
- Resource metadata is described by an external LinkML schema. A profile YAML names that schema and one class, chooses the search fields, and chooses the fields copied into the search document.
- When several ETL plugins are installed, the first plugin whose `validate` returns true handles the file. Plugins are checked against a contract. There is no confidence score.
- The reference authentication provider is Keycloak. A deployment may replace it.
- The reference server process reads the incarnation spec and launches Tiled in a form that satisfies this document, on a Tiled release that contains the entity/link graph.

## Responsibilities

The data server:

- reports its specification version, server version, and installed plugins
- searches the catalog and, in the reference representation, returns an RO-Crate
- lists catalog resources
- returns one resource, its metadata, and a data descriptor
- requires a bearer token that the active auth provider accepts
- leaves catalog bytes and source files unchanged

The data server does not:

- accept writes of a catalog record or a file, derived resources, or client edits
- walk a raw directory, choose a plugin, extract metadata, copy payloads, or merge a staging catalog
- modify files that the catalog points at
- embed modality vocabulary
- let a project module replace the search route

Those ETL steps are specified in [plugins.md](plugins.md) and implemented by the reference pipeline in [actions.md](actions.md). They are not routes on this server.

## Reference process

The reference data server (`tegel-dataserver`) is a process, not an unmodified `tiled serve`. At startup it:

1. loads the search profile from `TEGEL_PROFILE` and the LinkML schema that profile names under `TEGEL_SCHEMA_ROOT`, and rejects a profile field that is not a slot of the named class
2. opens the sqlite catalog at `TEGEL_CATALOG_URI`, creating it when absent
3. loads every installed entry point; a plugin that fails to load stops startup
4. builds the selected auth provider, the `tiled` index, and the `filesystem` storage
5. builds Tiled's application with that one auth provider and anonymous access off, then mounts the read API below on it

An incarnation changes what that process loads. It does not change the HTTP contract.

The process depends on `tiled[server]>=0.2.15`. It refuses to start when `tiled.graph` cannot be imported, when the installed Tiled is older than 0.2.15, or when the catalog database has no `entities` table. `tegel-core` does not depend on Tiled. The reference server package does.

The graph is Tiled's store for links between catalog nodes and other entities. This document's read API returns `relations` from the stored row's `tegel` block, not from the graph. Tiled's own routes show the whole stored row, `tegel` block included.

Tiled's own routes stay mounted beside the read API: `/api/v1/...`, `/api/graphql`, `/zarr/...`, and Tiled's UI. They are Tiled's interface, not part of this contract. A token the auth provider accepts is given the scopes `read:metadata` and `read:data` only, so Tiled's write routes reject it.

### Configuration

`python -m tegel.dataserver` reads its configuration from the environment. There is no default host, origin, issuer, or secret.

| Variable | Meaning |
| --- | --- |
| `TEGEL_PUBLIC_ORIGIN` | Base URL used in resource URIs. Required. |
| `TEGEL_CATALOG_URI` | sqlite catalog URI, for example `sqlite:////output/catalog.db`. Required. |
| `TEGEL_PROFILE` | Path to the search profile YAML. Required. |
| `TEGEL_SCHEMA_ROOT` | Directory the profile's `schema_path` is resolved under. Required. |
| `TEGEL_OIDC_ISSUER` | Token issuer URL. Required. |
| `TEGEL_OIDC_AUDIENCE` | Expected token audience. Required. |
| `TEGEL_OIDC_CLIENT_ID` | OIDC client id. Required. |
| `TEGEL_OIDC_CLIENT_SECRET` | OIDC client secret. Optional. |
| `TEGEL_SESSION_SECRET` | Secret Tiled signs its own session tokens with. Required. |
| `TEGEL_AUTH` | Name of the `tegel.auth` entry point. Default `keycloak`. |
| `TEGEL_HOST`, `TEGEL_PORT` | Bind address. Default `127.0.0.1:8000`. |
| `TEGEL_ROOT_PATH` | Path prefix when the process is mounted behind a proxy. Optional. |

## HTTP API

Routes are relative to the server's configured base URL. A client stores that base URL; this specification does not name a host or port.

Every route below asks the active auth provider to accept the `Authorization` header. A missing or rejected token is `401` with `{"detail": "Authentication required"}`.

Errors use `{"detail": "<message>"}`.

| Route | Success |
| --- | --- |
| `GET /capabilities` | `Capabilities` JSON |
| `GET /search` | Reference: RO-Crate, `application/ld+json` |
| `GET /resources` | JSON list of resource references |
| `GET /resources/{id}` | `Resource` JSON |
| `GET /resources/{id}/metadata` | The record block, a JSON object |
| `GET /resources/{id}/data` | `DataDescriptor` JSON |
| `GET /resources/{id}/data/content` | Stored bytes, for the `bytes` family only |
| `POST /refresh-catalog` | `{"refreshed": true}` |

`GET /resources/{id}/data/content` returns the bytes at the resource's `payload_uri`, opened by the storage backend, with the descriptor's media type. When the descriptor's family is not `bytes`, the response is `404` with `{"detail": "Content is only available for bytes"}`. For `array`, `table`, `container`, or `sparse`, the descriptor is the data access contract. The server does not offer a single response that contains the whole array.

`POST /refresh-catalog` is an operational route. It disposes Tiled's pooled catalog connections and opens a new pool, so a catalog that another process merged is visible. It writes nothing. It is the only non-GET route. When the catalog is not database-backed, the response is `409` with `{"detail": "Catalog is not database-backed"}`.

Unknown routes are `404`. An unknown `{id}`, or an id that contains `/` or `\`, is `404` with `{"detail": "Resource not found"}`. When no installed reader validates the resource, `GET /resources/{id}/data` and `/data/content` are `404` with `{"detail": "No reader validated the resource"}`.

### Capabilities

`GET /capabilities` returns:

- `spec_version` — `SPEC_VERSION` from `tegel.core.version`
- `server_version` — the implementation's version
- `plugins` — installed entry points (`group`, `name`, `version`) across every group
- `search_document` — `ro-crate` for the reference server, or another token a downstream specification defines
- `auth` — the selected provider's name, `keycloak` for the reference server
- `schema_id`, `schema_version`, `class` — optional; the reference server reports the loaded profile's values

A client uses `search_document` to decide how to read `GET /search`. It does not assume every server returns an RO-Crate.

### Search

`GET /search` takes query parameters. Each parameter is a field name and a string value. Parameters combine with AND. Each parameter is compared with the record block's value for that field:

- a stored boolean matches the query `true` or `false`, exactly and lowercase
- a stored string matches when it equals the query
- any other stored value, and a missing field, never matches

Every parameter must be listed in the active profile's `search` sequence. An unknown parameter is `400` with `{"detail": "Unknown search field <names>"}`. An empty query returns every catalog resource.

There is no paging parameter.

When `search_document` is `ro-crate`, the body is an RO-Crate 1.1 document (`application/ld+json`):

- `@context` is `https://w3id.org/ro/crate/1.1/context`
- the metadata file entity `ro-crate-metadata.json` conforms to RO-Crate 1.1 and to the profile `id`
- the root dataset `./` is named by the profile `name` and has `hasPart` entries for the matches
- each match is a `Dataset` whose `@id` and `contentUrl` are the resource URL `{public_origin}/resources/{id}`
- `name`, `identifier`, and `dateCreated` come from the record through the profile `entity` mapping; `name` and `identifier` fall back to the resource id
- `additionalProperty` has one `PropertyValue` for each name in the profile `properties` list, valued from the record

`public_origin` comes from server configuration. The specification does not default it.

A server that advertises another `search_document` token returns `SearchResult` JSON (`application/json`) with `document`, `count`, and `hits`, unless the downstream specification that defines the token replaces the body. `DataClient.search` parses either body. The reference server only builds the RO-Crate; configured with another token, it answers `GET /search` with `404` and `{"detail": "Search document is not available"}`.

The reference server does not withdraw the search route because a project module is installed.

### Browse, resource, metadata, data

`GET /resources` returns `{"resources": [ResourceRef, ...], "count": N}`, ordered by id. Each reference has `id`, `uri`, and, when the catalog recorded one, `type`.

`GET /resources/{id}` returns one `Resource`.

`GET /resources/{id}/metadata` returns the row's `record` block as stored. The `tegel` block is not part of that body.

`GET /resources/{id}/data` returns the `DataDescriptor` from the first reader whose `validate` is true:

- `family` is a Tiled structure family: `array`, `table`, `container`, `sparse`, or `bytes`
- `media_type` is the payload type
- `content_url` is present for `bytes` and may be present for other families when the storage backend exposes one
- `shape`, `chunks`, and `data_type` describe an array without loading it

A reader chooses the family and the media type. A reader may return `array` with `chunks` so a large cube is described and not loaded into memory.

## Resource model

### Stored row

Every catalog node's metadata has exactly two top-level keys:

```json
{
  "record": {
    "uuid": "rec-derived",
    "sample_name": "red-record",
    "channel": "red",
    "calibrated": false
  },
  "tegel": {
    "plugin_id": "toy-record",
    "source_name": "derived.npy",
    "resource_type": "toy.record",
    "resource_type_version": "1",
    "payload_uri": "file:///data/derived.npy",
    "relations": [{"rel": "derived-from", "target": "rec-raw"}],
    "schema_id": "https://w3id.org/tegel/toy/record",
    "schema_version": "2",
    "class": "Record"
  }
}
```

- `record` holds the domain fields. Every key is a slot of the profile's class. Values are stored as the producer wrote them: strings, numbers, booleans, lists.
- `tegel` (`CatalogEnvelope`) is built by staging with `tegel.core.plugins.catalog_metadata`. An extractor never writes it. `plugin_id` is the extractor's, `source_name` is the input file's name, `resource_type_version` is the registered type's version, and the schema fields are the profile's. `payload_uri` is optional; `relations` defaults to `[]`.

The reference index serves only rows of this shape. Any other row is skipped, and a warning on the `tegel.index` logger says it must be re-ingested.

### Ids and resources

A resource id is the catalog node key (`tegel.core.contracts.resource_id_for`). It is the caller's `options["key"]` when that is a non-empty string, otherwise the record's value for the profile's `entity.identifier`. The id is a non-empty string with no surrounding whitespace, no `/`, and no `\`. Nothing derives an id from a filename.

`uri` is `{public_origin}/resources/{id}`, with the id percent-encoded.

`Resource` fields:

- `id`, `uri`
- `type` and `type_version` from the `tegel` block's `resource_type` and `resource_type_version`
- `name`, `identifier`, `date_created` from the record through the profile entity mapping
- `metadata` — the `record` block
- `relations` — the `tegel` block's `relations`
- `provenance` — `plugin_id` and `source_name` from the `tegel` block
- `payload_uri` — the `tegel` block's `payload_uri`, or none

Provenance and relations come from the `tegel` block, which staging builds. There is no request that submits them.

The data server stores and returns the record. It does not convert the schema to JSON Schema in order to accept a write.

Relations are how a catalog records that one resource points at another, for example a derived product and its source. The ETL pipeline writes them. The server returns them.

### Types and profiles

A resource type has:

- `name`
- `version`
- `metadata_schema` — the LinkML schema id
- `parent` — optional type name

`parent` names a fallback type. `tegel.core.contracts.type_chain` walks the chain and raises on a cycle. The reference server's reader selection does not walk it: each reader's `validate` sees the resource and decides.

One server serves one profile, one schema, and one class. At startup every installed `tegel.resource_types` entry must have `metadata_schema` equal to the profile's `schema_id`; otherwise startup stops with `resource type <name> uses schema <a>; the profile names <b>`. Startup also stops when no resource type is installed. A resource type's `version` is the incarnation's mapping version. It is independent of `schema_version`.

A search profile has:

- `id`, `name`
- `schema_id` — must equal the loaded LinkML document's `id`
- `schema_version` — must equal that document's `version`; both are quoted YAML strings
- `schema_path` — the document's path relative to `TEGEL_SCHEMA_ROOT`
- `class` — the class whose slots define a record
- `search` — field names a client may pass to `GET /search`
- `properties` — field names copied into the RO-Crate as `additionalProperty`
- `entity.name`, `entity.identifier`, `entity.dateCreated` — record fields for the Dataset `name`, `identifier`, and `dateCreated`; `entity.identifier` also gives the catalog id

The server loads `$TEGEL_SCHEMA_ROOT/<schema_path>` as YAML. An absolute `schema_path`, a path that leaves the root, a mismatched `id` or `version`, or a missing class is an error. A schema `version` that is not a YAML string is an error (`schema version must be a quoted string in <path>: got <type> <value>`), and so is a profile `schema_version` that is not one (`schema_version must be a quoted string in the profile: got <type> <value>`). The schema is not fetched over the network. An `imports` entry other than `linkml:*` is an error, because imports are not followed.

The slots of a class are read in this order, keeping the first definition of each name:

1. its `is_a` ancestors, recursively, ancestors first
2. its `mixins`, in listed order, each expanded the same way (a mixin's own `is_a` and `mixins` first)
3. its own `slots` list, defined in the document's top-level `slots`
4. its own `attributes`

Then `slot_usage` on each class, in that order, refines the definitions. A name in a class's `slots` list that the top-level `slots` does not define is an error (`slot <name> on class <class> is not defined in the schema`). A cycle through `is_a` or `mixins`, and a missing class or mixin, are errors.

A profile field that is not one of those slots is rejected at startup. At ETL time, a record with a key that is not a slot is rejected. The record block holds only slots of the class; Tegel's own fields live in the `tegel` block. `tegel-core` reads the schema with PyYAML, does not depend on the LinkML runtime, and does not check value types.

JSON Schema files under `specs/generated/` describe the pydantic wire models. They are not the authoring format for a resource type. `specs/generated/openapi.json` lists the GET routes only. It does not list the operational `POST /refresh-catalog`.

## Plugin axes

Two axes are independent.

**Storage** decides where bytes live. A backend registered on `tegel.storage` opens bytes for a URI the catalog stored. The reference backend, `filesystem`, opens local `file:` URIs. The data server asks storage to open bytes for a `bytes` descriptor. It does not prescribe a copy step.

**Modality** decides what bytes mean: resource types (`tegel.resource_types`), readers (`tegel.readers`), and previewers (`tegel.previewers`). ETL plugins (`tegel.extractors`) also belong to the modality axis, and they run in the downstream ETL application.

Auth (`tegel.auth`) and the catalog index (`tegel.index`) are replaceable production concerns. They are not modality plugins.

## ETL contract

The data server does not run this contract. The ETL application does. It is specified here so incarnations and the reference ETL share one checker. Details of discovery and the checker are in [plugins.md](plugins.md).

An extractor plugin:

- constructs with no arguments
- exposes a non-empty unique `plugin_id`
- implements `validate(input_path) -> bool`
- implements `process(input_path, options) -> ProcessedDataPackage`

`validate` is a bool. The application asks plugins in entry-point name order and keeps the first true result. Later plugins are not called. If none return true, the file is not ingested.

`ProcessedDataPackage` carries:

- `payload_path` — a readable file
- `record` — the domain fields; every key is a slot of the profile's class
- `resource_type` — a registered `ResourceType.name`
- `payload_uri` — optional location of the payload, served by `/data/content` for `bytes`
- `relations` — `{rel, target}` objects, default `[]`
- `metrics` — a dict of non-empty string keys to floats

The extractor does not write `plugin_id`, `source_name`, or version fields. Staging adds them in the `tegel` block.

The catalog id for the package is the id rule above. A package whose record has no value for `entity.identifier`, and no caller `options["key"]`, is rejected.

Re-running ETL for an id that is already in the catalog replaces that catalog record. That is an ETL write, not a data-server route.

Copying `payload_path` into a payload directory is allowed and is not required. The ETL application chooses. This specification does not check which choice it made.

## Data access

A reader implements `validate(resource) -> bool` and `describe(resource) -> DataDescriptor`. Selection is the same first-true rule, in entry-point name order. Readers construct with no arguments.

`describe` returns a structure family. It does not load an `array` into memory. `chunks` on an array descriptor is the chunk grid the storage backend can serve. `GET /resources/{id}/data/content` is only defined for `bytes`.

A previewer implements `validate(resource) -> bool` and `preview(resource) -> DataDescriptor` for a representation of the resource. The reference server lists installed previewers in `GET /capabilities` and does not call them.

## Index

The index is the catalog the server reads. `tegel.index` provides `get(resource_id)` and `keys()`. The reference index, `tiled`, reads node keys and metadata from the sqlite catalog that the ETL application committed. The data server's index interface is read-only. A `TiledCatalogIndex` built with no profile, for example by the `tegel.index` entry point constructed with no arguments, serves resources without `name`, `identifier`, and `date_created`; the reference server always passes the profile.

Underlying files are not modified by a read. Whether ETL copied them earlier is outside this server.

When another process writes the SQLite catalog, the reference server sees the new rows after `POST /refresh-catalog`. That route writes nothing. It is not a `DataClient` method.

## Authentication

`tegel.auth` provides a `name` and `authenticate(authorization) -> Principal`. `Principal` has a `subject` string.

The reference provider, `keycloak`, accepts RS256 bearer tokens signed by a key from the issuer's discovery document (`{issuer}/.well-known/openid-configuration`, then its `jwks_uri`), with the configured issuer and audience and a non-empty `sub`. Issuer, audience, and client id come from `TEGEL_OIDC_*`. There is no default host, realm name, or secret. Anonymous access is off.

A site replaces the provider by registering a different `tegel.auth` entry point and setting `TEGEL_AUTH` to its name. The server gives that provider to Tiled through one adapter. Keycloak is not left configured beside it. `Capabilities.auth` is the selected provider's name.

The client sends `Authorization: Bearer <token>`. `DataClient` can be given a token directly, or a token URL plus client id and client secret so it performs an OAuth2 client-credentials grant. Those values come from the caller. The client has no built-in secret and no write method.

## Versioning and compatibility

See [specs/README.md](README.md). `GET /capabilities` is the handshake. `DataClient.capabilities` raises when the server's `spec_version` major component differs from its own. Before `1.0.0`, treat a minor mismatch as a reason to stop and report the two versions.
