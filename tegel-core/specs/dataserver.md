# Data server

The data server reads a catalog that an ETL pipeline has already written and serves it over HTTP. This document is the contract for that read API, the resource model, and the boundaries around ETL, storage, and authentication.

The behavior below is taken from the Personalized Data Server Platform in ProductionLambda, then narrowed by these decisions:

- The HTTP API is read-only. Read-only means no route creates, changes, or deletes a catalog record or a file.
- Filling the catalog, including whether payload bytes are copied, is a downstream ETL application. This specification does not require copying and does not forbid it.
- The reference search document is an RO-Crate. Another specification may define a different document.
- Resource metadata is authored as LinkML YAML. A profile YAML chooses the search fields and the fields copied into the search document.
- When several ETL plugins are installed, the first plugin whose `validate` returns true handles the file. Plugins are checked against a contract. There is no confidence score.
- The reference authentication provider is Keycloak. A deployment may replace it.
- `tools/kg` is not part of this specification.
- The reference server process reads the incarnation spec and launches Tiled in a form that satisfies this document.
- That Tiled release must contain the entity/link graph. The oldest such release is 0.2.15. Any later release that still ships the graph is acceptable.

## Responsibilities

The data server:

- reports its specification version, server version, and installed plugins
- searches the catalog and, in the reference representation, returns an RO-Crate
- lists catalog resources
- returns one resource, its metadata, and a data descriptor
- requires a bearer token when the active auth provider is Keycloak
- leaves catalog bytes and source files unchanged

The data server does not:

- accept writes of a catalog record or a file, derived resources, or client edits
- walk a raw directory, choose a plugin, extract metadata, copy payloads, or merge a staging catalog
- modify files that the catalog points at
- embed modality vocabulary
- let a project module replace the search route

Those ETL steps are specified in [plugins.md](plugins.md) so an incarnation can implement them. They are not routes on this server.

## Reference process

The reference data server is a process, not an unmodified `tiled serve`. It reads the installed incarnation spec — entry points, the LinkML schema, and the search profile — and launches Tiled already shaped to this document: the read API above, the search route left in place, Keycloak unless replaced, and the structure families a reader reports.

An incarnation changes what that process loads. It does not change the HTTP contract.

The process depends on Tiled's server extra at a release that includes the entity/link graph (`tiled.graph`, catalog tables `entities` and `links`). That graph first shipped in Tiled 0.2.15 and is present in the stable releases through 0.2.18. The requirement is the graph, so the floor is `tiled[server]>=0.2.15` and a later release stays in range only while it still ships the graph. `tegel-core` does not depend on Tiled. The reference server package does.

The graph is Tiled's store for links between catalog nodes and other entities. This document's read API still returns `relations` projected from catalog metadata. It does not add a GraphQL route.

## HTTP API

Routes are relative to the server's configured base URL. A client stores that base URL; this specification does not name a host or port.

All routes require authentication when the active provider is Keycloak. A missing or rejected token is `401` with `{"detail": "Authentication required"}`.

Errors use `{"detail": "<message>"}`.

| Route | Success |
| --- | --- |
| `GET /capabilities` | `Capabilities` JSON |
| `GET /search` | Reference: RO-Crate, `application/ld+json` |
| `GET /resources` | JSON list of resource references |
| `GET /resources/{id}` | `Resource` JSON |
| `GET /resources/{id}/metadata` | Catalog metadata JSON object |
| `GET /resources/{id}/data` | `DataDescriptor` JSON |
| `POST /refresh-catalog` | `{"refreshed": true}` |

`GET /resources/{id}/data/content` returns the stored bytes when the descriptor's structure family is `bytes`. For `array`, `table`, `container`, or `sparse`, the descriptor is the data access contract. The server does not offer a single response that contains the whole array.

`POST /refresh-catalog` is an operational route. It reopens the catalog after the ETL merge. It writes nothing. It requires the same authentication as every other route. It is the only non-GET route. When the catalog is not database-backed, the response is `409` with `{"detail": "Catalog is not database-backed"}`.

Unknown routes are `404`. An unknown `{id}` is `404` with `{"detail": "Resource not found"}`.

### Capabilities

`GET /capabilities` returns:

- `spec_version` — `SPEC_VERSION` from this package
- `server_version` — the implementation's version
- `plugins` — installed entry points (`group`, `name`, `version`)
- `search_document` — `ro-crate` for the reference server, or another token a downstream specification defines
- `auth` — `keycloak` for the reference server, or the replacement provider's name

A client uses `search_document` to decide how to read `GET /search`. It does not assume every server returns an RO-Crate.

### Search

`GET /search` takes query parameters. Each parameter is a field name and a string value. Parameters combine with AND. Comparison is equality with the value stored in catalog metadata, using the same rule as the reference implementation: the stored value must equal the query string.

Every parameter must be listed in the active profile's `search` sequence. An unknown parameter is `400` with a `detail` that names the field. An empty query returns every catalog resource the caller is allowed to see.

There is no paging parameter. `count` is the number of matches.

When `search_document` is `ro-crate`, the body is an RO-Crate 1.1 document (`application/ld+json`):

- `@context` is `https://w3id.org/ro/crate/1.1/context`
- the metadata file entity conforms to RO-Crate 1.1 and to the profile `id`
- the root dataset `./` has `hasPart` entries for the matches
- each match is a `Dataset` whose `@id` and `contentUrl` are the resource URL `{public_origin}/resources/{id}`
- `name`, `identifier`, and `dateCreated` come from the profile `entity` mapping
- `additionalProperty` has one `PropertyValue` for each name in the profile `properties` list

`public_origin` comes from server configuration. The specification does not default it.

When `search_document` is any other token, the body is `SearchResult` JSON (`application/json`) with `document`, `count`, and `hits`. A downstream specification defines that token and may replace the body. Clients that only implement `ro-crate` must read `search_document` before parsing.

The reference server does not withdraw the search route because a project module is installed.

### Browse, resource, metadata, data

`GET /resources` returns `{"resources": [ResourceRef, ...], "count": N}`. Each reference has `id`, `uri`, and, when the catalog recorded one, `type`.

`GET /resources/{id}` returns one `Resource`.

`GET /resources/{id}/metadata` returns the catalog metadata object as stored. The server does not rewrite it on read.

`GET /resources/{id}/data` returns a `DataDescriptor`:

- `family` is a Tiled structure family: `array`, `table`, `container`, `sparse`, or `bytes`
- `media_type` is the payload type
- `content_url` is present for `bytes` and may be present for other families when the storage backend exposes one
- `shape`, `chunks`, and `data_type` describe an array without loading it

A reader chooses the family. The reference catalog records external `bytes` with media type `application/octet-stream`. An incarnation reader may return `array` with `chunks` so a large cube is described and not loaded into memory.

## Resource model

A resource id is a non-empty string taken from catalog metadata: `options["key"]` when the ETL caller set it, otherwise metadata `key` or `uuid`. The id has no slash. The server does not invent an id from a filename.

`uri` is `{public_origin}/resources/{id}`.

`Resource` fields:

- `id`, `uri`
- `type` and `type_version` when a resource type was recorded
- `name`, `identifier`, `date_created` when a profile entity mapping applies
- `metadata` — the catalog metadata object, including the modality block
- `relations` — zero or more `{rel, target}` objects projected from `metadata["relations"]` when that value is a list of those objects
- `provenance` — present when metadata has non-empty string `plugin_id` and `source_name`

`provenance` is a read-only projection of catalog fields. There is no request that submits provenance.

The modality block is the metadata object validated, at ETL time, against the resource type's LinkML schema. The data server stores and returns that object. It does not convert the schema to JSON Schema in order to accept a write.

Relations are how a catalog records that one resource points at another, for example a derived product and its source. The ETL pipeline writes them. The server returns them.

### Types and profiles

A resource type has:

- `name`
- `version`
- `metadata_schema` — the LinkML schema id
- `parent` — optional type name

`parent` is a fallback chain. A reader or previewer that does not recognize a type walks parents until one is handled. Cycles are an error.

A search profile has:

- `id`, `name`
- `schema` — path or id of the LinkML schema, resolved next to the profile by the server
- `search` — field names a client may pass to `GET /search`
- `properties` — field names copied into the RO-Crate as `additionalProperty`
- `entity.name`, `entity.identifier`, `entity.dateCreated` — metadata keys for the Dataset `name`, `identifier`, and `dateCreated`

The server loads the LinkML document as YAML and reads attribute names from the `tree_root` class, or from the only class when the document has one class. A profile slot that is not one of those attributes is rejected at startup. This package does not depend on the LinkML runtime.

JSON Schema files under `specs/generated/` describe the pydantic wire models. They are not the authoring format for a resource type.

## Plugin axes

Two axes are independent.

**Storage** decides where bytes live: a filesystem tree, a Tiled catalog, or another backend registered on `tegel.storage`. The data server asks storage to open bytes for a `bytes` descriptor. It does not prescribe a copy step.

**Modality** decides what bytes mean: resource types (`tegel.resource_types`), readers (`tegel.readers`), and previewers (`tegel.previewers`). ETL plugins (`tegel.extractors`) also belong to the modality axis, and they run in the downstream ETL application.

Auth (`tegel.auth`) and the catalog index (`tegel.index`) are replaceable production concerns. They are not modality plugins.

## ETL contract

The data server does not run this contract. The ETL application does. It is specified here so incarnations and the reference ETL share one checker. Details of discovery and the checker are in [plugins.md](plugins.md).

An extractor plugin:

- constructs with no arguments
- exposes a non-empty unique `plugin_id`
- implements `validate(input_path) -> bool`
- implements `process(input_path, options) -> ProcessedDataPackage`

`validate` is a bool. The application asks plugins in entry-point name order and keeps the first true result. Later plugins are not called. If none return true, ingestion of that file fails.

`ProcessedDataPackage` carries:

- `payload_path` — a readable file
- `metadata` — a dict that includes `plugin_id` equal to the plugin and a non-empty `source_name`
- `metrics` — a dict of non-empty string keys to floats

The catalog id for the package is the explicit id rule above. A package with no `key` or `uuid`, and no caller `options["key"]`, is rejected.

Re-running ETL for an id that is already in the catalog replaces that catalog record. That is an ETL write, not a data-server route.

Copying `payload_path` into a payload directory is allowed and is not required. The ETL application chooses. This specification does not check which choice it made.

## Data access

A reader implements `validate(resource) -> bool` and `describe(resource) -> DataDescriptor`. Selection is the same first-true rule, in entry-point name order.

`describe` returns a structure family. It does not load an `array` into memory. `chunks` on an array descriptor is the chunk grid the storage backend can serve. `GET /resources/{id}/data/content` is only defined for `bytes`.

Previewers use the same selection rule and return a `DataDescriptor` for a representation of the resource. The reference server has no previewer installed.

## Index

The index is the catalog the server reads. `tegel.index` provides `get` and `keys`. The reference index is a Tiled catalog that includes the entity/link graph, committed by the ETL application. The data server's index interface is read-only.

Underlying files are not modified by a read. Whether ETL copied them earlier is outside this server.

If an implementation serves a SQLite catalog that another process writes, reopening pooled connections is an operational concern of that implementation. The reference server does this with `POST /refresh-catalog`, which writes nothing. It is not a `DataClient` method.

## Authentication

`tegel.auth` provides `authenticate(authorization) -> Principal`. `Principal` has a `subject` string.

The reference provider is Keycloak bearer tokens. Configuration is the issuer URL, audience, and client id, all from the environment or a local config file. This specification does not set a default host, realm name, or secret. Anonymous access is off in the reference server.

A site replaces the provider by registering a different `tegel.auth` entry point and selecting that name. The server gives that provider to Tiled through one adapter. Keycloak is not left configured beside it. `Capabilities.auth` is the selected provider's name.

The client sends `Authorization: Bearer <token>`. It can be given a token directly, or a token URL plus client id and client secret so it can perform an OAuth2 client-credentials grant. Those values come from the caller. The client has no built-in secret.

## Versioning and compatibility

See [specs/README.md](README.md). `GET /capabilities` is the handshake. A client should compare `spec_version` major component with its own before calling search or data routes. Before `1.0.0`, treat a minor mismatch as a reason to stop and report the two versions.
