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
- returns one resource, its metadata, and its data parts, each with one content URL
- serves byte parts and the payload with HTTP Range, and streams `proxy` parts from their upstream
- requires a bearer token that the active auth provider accepts
- leaves catalog bytes and source files unchanged

The data server does not:

- accept writes of a catalog record or a file, derived resources, or client edits
- walk a raw directory, choose a plugin, extract metadata, copy payloads, or merge a staging catalog
- call a reader on a read request; parts are described once, at staging
- modify files that the catalog points at
- embed modality vocabulary
- let a project module replace the search route

Those ETL steps are specified in [plugins.md](plugins.md) and implemented by the reference pipeline in [actions.md](actions.md). They are not routes on this server.

## Reference process

The reference data server (`tegel-dataserver`) is a process, not an unmodified `tiled serve`. At startup it:

1. loads the search profile from `TEGEL_PROFILE` and the LinkML schema that profile names under `TEGEL_SCHEMA_ROOT`, and rejects a profile field that is not a slot of the named class
2. opens the sqlite catalog at `TEGEL_CATALOG_URI`, creating it when absent, with Tiled's readable storage set to `TEGEL_READABLE_STORAGE`
3. loads every installed entry point; a plugin that fails to load stops startup
4. builds the selected auth provider, the `tiled` index, and the `filesystem` storage, and loads the upstreams file named by `TEGEL_UPSTREAMS`, if any
5. builds Tiled's application with that one auth provider and anonymous access off, then mounts the read API below on it

An incarnation changes what that process loads. It does not change the HTTP contract.

The process depends on `tiled[server,formats]>=0.2.18`. It refuses to start when `tiled.graph` cannot be imported, when the installed Tiled is older than 0.2.15, or when the catalog database has no `entities` table. `tegel-core` does not depend on Tiled. The reference server package does.

The graph is Tiled's store for links between catalog nodes and other entities. This document's read API returns `relations` from the stored row's `tegel` block, not from the graph. Tiled's own routes show the whole stored row, `tegel` block included.

Tiled's own routes stay mounted beside the read API: `/api/v1/...`, `/api/graphql`, `/zarr/...`, and Tiled's UI. Array and table parts are read on Tiled's `/api/v1/array/...` and `/api/v1/table/...` routes through their `content_url` (see [Data parts](#data-parts)). Beyond those URLs, Tiled's routes are Tiled's interface, not part of this contract. A token the auth provider accepts is given the scopes `read:metadata` and `read:data` only, so Tiled's write routes reject it with `401`.

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
| `TEGEL_ROOT_PATH` | Path prefix when the process is mounted behind a proxy. Optional. `TEGEL_PUBLIC_ORIGIN` already includes it. |
| `TEGEL_READABLE_STORAGE` | Absolute directories, separated by `os.pathsep`, that Tiled may read externally managed assets from. Default: `TEGEL_RECORDS`. With neither, there are no roots, which is only valid when no part is served by Tiled. |
| `TEGEL_UPSTREAMS` | Path of the upstreams YAML file for remote parts. Optional; unset means no remote part is served. |
| `TEGEL_UPSTREAM_TIMEOUT` | Seconds to wait for an upstream. Default `10`. |

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
| `GET /resources/{id}/data` | `DataDescriptor` JSON: the parts and the payload, each with `content_url` |
| `GET /resources/{id}/data/content` | The payload's bytes, with HTTP Range |
| `GET /resources/{id}/data/{part}/content` | A Tegel-served part's bytes, with HTTP Range, or a `proxy` part streamed from upstream |
| `POST /refresh-catalog` | `{"refreshed": true}` |

The data routes are specified in [Data parts](#data-parts). The server does not offer a single Tegel response that contains a whole Tiled-served array; clients slice it through its `content_url`.

`POST /refresh-catalog` is an operational route. It disposes Tiled's pooled catalog connections and opens a new pool, so a catalog that another process merged is visible. It writes nothing. It is the only non-GET route. When the catalog is not database-backed, the response is `409` with `{"detail": "Catalog is not database-backed"}`.

Unknown routes are `404`. An unknown `{id}`, or an id that contains `/` or `\`, is `404` with `{"detail": "Resource not found"}`.

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

`GET /resources/{id}` returns one `Resource`, including `parts` and `payload` as stored, without `content_url`.

`GET /resources/{id}/metadata` returns the row's `record` block as stored. The `tegel` block is not part of that body.

`GET /resources/{id}/data` returns the stored parts and payload with `content_url` filled in. No reader is called.

## Data parts

A resource's data is a list of named parts, plus an optional payload. A reader describes them once, at staging ([actions.md](actions.md)); the server serves what staging stored.

### `DataPart`

| Field | Type | Notes |
| --- | --- | --- |
| `name` | string | one path segment matching `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`, unique within the resource |
| `family` | Tiled structure family | what the reader expects: `array`, `table`, `bytes`, `container`, `sparse`, … |
| `media_type` | string | chooses the Tiled adapter, for example `application/x-zarr`, `text/csv;header=present`, `text/plain` |
| `asset_uri` | string | a `file:` URI of the file or directory, or for a remote part an upstream Tiled node URL `{origin}{api_prefix}/metadata/{path}` |
| `dataset` | string, optional | a path inside the asset, for example an HDF5 dataset; passed to Tiled's adapter as `dataset` |
| `remote` | `link`, `proxy`, or none | required for an http(s) `asset_uri`, forbidden for `file:` |
| `designated` | bool, default false | the part a person should look at first; at most one per resource |
| `title` | string, optional | human label |
| `shape`, `chunks`, `data_type` | optional | array hints from headers; staging fills them from Tiled's structure when the reader leaves them out |
| `columns` | list of strings, optional | table hint; filled the same way |
| `size` | int, optional | bytes of a single-file part; set by staging |
| `served_by` | `tiled`, `tegel`, `link`, `proxy` | set by staging, never by the reader |
| `upstream`, `remote_path` | strings | set by staging for remote parts: the origin and the node path after `/metadata/` |
| `content_url` | string | set by the server on read; never stored |

`DataDescriptor` is `{"parts": [DataPart, ...], "payload": DataPart | null}`. Part names are unique, and at most one part is designated. The payload is a byte part named `payload` whose `asset_uri` is the resource's `payload_uri`.

### Who serves a part

At staging, a `file:` part whose `media_type` has a Tiled registration adapter that registers one URI (`DEFAULT_REGISTRATION_ADAPTERS_BY_MIMETYPE` entries with `from_uris`, except `application/octet-stream` and `multipart/*`) becomes an externally managed child node of the resource, keyed by the part name. Its structure comes from that adapter. Its `served_by` is `tiled`. Every other `file:` part is `served_by: tegel` and has no child node. The resource node is a `container` whose metadata is the stored row.

In Tiled 0.2.18 that means:

| Format | Media type | Served by | Tiled adapter |
| --- | --- | --- | --- |
| Zarr v3 array (directory) | `application/x-zarr` | Tiled, `array` | `ZarrArrayAdapter`; structure from `zarr.json` |
| HDF5 dataset | `application/x-hdf5` with `dataset` | Tiled, `array` | `HDF5ArrayAdapter`; external links resolve |
| npy | `application/x-npy` | Tiled, `array` | `NPYAdapter` |
| TIFF, JPEG | `image/tiff`, `image/jpeg` | Tiled, `array` | `TiffAdapter`, `JPEGAdapter` |
| CSV | `text/csv`, `text/csv;header=present` | Tiled, `table` | `CSVAdapter`; reads the file at staging |
| Excel | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | Tiled, `container` | `ExcelAdapter`; reads the file at staging |
| Parquet, Arrow, NetCDF, anything else | | Tegel, bytes | none: Tiled 0.2.18 cannot register these from a URI |

Known limitation: Parquet, Arrow and NetCDF parts are byte parts in 0.4.0, because Tiled 0.2.18's adapters for them have no `from_uris`. Domain formats with no Tiled adapter are byte parts too.

### Content URLs

Built per request from `TEGEL_PUBLIC_ORIGIN` (which includes the root path) and Tiled's API prefix, `{tiled_base} = {public_origin}/api/v1`:

| Part | `content_url` | Client usage |
| --- | --- | --- |
| `tiled`, family `array` | `{tiled_base}/array/full/{id}/{name}` | `?slice=0:10,::2` and `format=`; also `{tiled_base}/array/block/{id}/{name}?block=i,j` |
| `tiled`, family `table` | `{tiled_base}/table/full/{id}/{name}` | `?column=a&column=b`; also `{tiled_base}/table/partition/{id}/{name}?partition=N` |
| `tiled`, other families | `{tiled_base}/node/full/{id}/{name}` | Tiled's own parameters |
| `tegel` | `{public_origin}/resources/{id}/data/{name}/content` | HTTP `Range: bytes=a-b` |
| `link`, family `array` / `table` / other | `{origin}{api_prefix}/array/full/{remote_path}` / `table/full` / `node/full` | the upstream's own auth |
| `proxy` | `{public_origin}/resources/{id}/data/{name}/content` | Tiled's query parameters, through Tegel |
| the payload | `{public_origin}/resources/{id}/data/content` | HTTP `Range` |

Tiled-served URLs need the same bearer token as the read API. A request without one is `401`.

### Byte serving

`GET /resources/{id}/data/{part}/content` serves a `tegel` part, and `GET /resources/{id}/data/content` serves the payload. Both read the file through the storage plugin:

- `Range: bytes=start-end`, `bytes=start-` and `bytes=-suffix` give `206` with `Content-Range: bytes start-end/size` and `Accept-Ranges: bytes`. Without `Range` the response is `200` with `Accept-Ranges: bytes`, streamed.
- An unsatisfiable range, and more than one range in a header, give `416` with `Content-Range: bytes */size`. Multiple ranges are not supported.
- The media type is the part's `media_type`.

Errors: an unknown part is `404 {"detail": "Part not found"}`. A Tiled-served part is `404 {"detail": "Part is served by Tiled; use content_url"}`. A `link` part is `404 {"detail": "Part is served by its upstream; use content_url"}`. A resource without a payload answers `/data/content` with `404 {"detail": "Resource has no payload"}`.

### Remote parts

A part may point at a node on another Tiled server instead of a local file. Its `asset_uri` is the node's metadata URL, `{origin}{api_prefix}/metadata/{path}`, and `remote` chooses how clients reach it:

- `link`: the `content_url` is the upstream Tiled URL. The client authenticates to the upstream itself; Tegel's token is never valid there, and `DataClient` does not send it there.
- `proxy`: the `content_url` is Tegel's part route, and Tegel streams the response from the upstream with its own credentials for that upstream.

**Upstreams file (`TEGEL_UPSTREAMS`).** Without it no remote part is allowed. Secrets are read from the environment variables the file names, never from the file:

```yaml
upstreams:
  - origin: https://upstream.example        # scheme://host[:port], no path
    api_prefix: /api/v1                     # default
    auth: api_key                           # none | api_key | client_credentials
    api_key_env: BEAMLINE_TILED_KEY         # sent as "Authorization: Apikey <key>"
  - origin: https://other.example
    auth: client_credentials
    token_url: https://auth.other.example/realms/x/protocol/openid-connect/token
    client_id: tegel
    client_secret_env: OTHER_TILED_SECRET   # token cached until it expires
```

**Proxy route.** `GET /resources/{id}/data/{part}/content` on a `proxy` part:

- An `array` part goes to `array/full`, or to `array/block` when `block` is given. A `table` part goes to `table/full`, or to `table/partition` when `partition` is given. Any other family goes to `node/full`.
- The upstream URL is built only from the stored `upstream` and `remote_path`. The caller cannot choose the host or the path.
- Only the query parameters `slice`, `block`, `expected_shape`, `format`, `column`, `field` and `partition` are forwarded, and only the `Accept` and `Range` headers. The caller's `Authorization` is never forwarded.
- The response is streamed, never buffered whole. `Content-Type`, `Content-Length`, `Content-Range`, `Accept-Ranges`, `Content-Encoding` and `ETag` are passed back.
- Upstream `2xx` and `416` pass through. `401` and `403` become `502 {"detail": "Upstream refused Tegel's credentials"}`. `404` stays `404`. A timeout becomes `504`, and a connection error or a `5xx` becomes `502`, with `{"detail": "Upstream <origin> failed: <reason>"}`.
- Only `GET` is proxied; other methods are `405`. No Tiled write route is used.

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
    "source_name": "record.json",
    "resource_type": "toy.record",
    "resource_type_version": "1",
    "payload_uri": "file:///records/derived/record.json",
    "relations": [{"rel": "derived-from", "target": "rec-raw"}],
    "schema_id": "https://w3id.org/tegel/toy/record",
    "schema_version": "2",
    "class": "Record",
    "parts": [
      {
        "name": "frames", "family": "array", "media_type": "application/x-zarr",
        "asset_uri": "file:///records/derived/frames.zarr", "designated": true,
        "title": "Frames", "shape": [4, 3], "chunks": [[2, 2], [3]], "data_type": "<i4",
        "served_by": "tiled"
      },
      {
        "name": "notes", "family": "bytes", "media_type": "text/plain",
        "asset_uri": "file:///records/derived/notes.txt", "designated": false,
        "title": "Notes", "size": 29, "served_by": "tegel"
      }
    ],
    "payload": {
      "name": "payload", "family": "bytes", "media_type": "application/json",
      "asset_uri": "file:///records/derived/record.json", "designated": false,
      "size": 156, "served_by": "tegel"
    }
  }
}
```

- `record` holds the domain fields. Every key is a slot of the profile's class. Values are stored as the producer wrote them: strings, numbers, booleans, lists.
- `tegel` (`CatalogEnvelope`) is built by staging with `tegel.core.plugins.catalog_metadata`. An extractor never writes it. `plugin_id` is the extractor's, `source_name` is the input file's name, `resource_type_version` is the registered type's version, and the schema fields are the profile's. `payload_uri` is optional; `relations` defaults to `[]`. `parts` and `payload` are the described parts with `served_by` set, stored without `content_url`.

The reference index serves only rows of this shape, with a `parts` key in the `tegel` block. Any other row, including a 0.3 row, is skipped, and a warning on the `tegel.index` logger says it is not a Tegel 0.4 row and must be re-ingested.

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
- `parts`, `payload` — the `tegel` block's stored parts and payload

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

**Storage** decides where bytes live. A backend registered on `tegel.storage` implements `open_bytes(uri)`, `open_range(uri, start, end)` (inclusive) and `size(uri)` for a URI the catalog stored. The reference backend, `filesystem`, opens local `file:` URIs. The data server uses storage for Tegel-served parts and the payload. It does not prescribe a copy step.

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
- `payload_uri` — optional location of the payload, served by `/data/content`
- `relations` — `{rel, target}` objects, default `[]`
- `metrics` — a dict of non-empty string keys to floats

The extractor does not write `plugin_id`, `source_name`, or version fields. Staging adds them in the `tegel` block.

The catalog id for the package is the id rule above. A package whose record has no value for `entity.identifier`, and no caller `options["key"]`, is rejected.

Re-running ETL for an id that is already in the catalog replaces that catalog record. That is an ETL write, not a data-server route.

Copying `payload_path` into a payload directory is allowed and is not required. The ETL application chooses. This specification does not check which choice it made.

## Data access

A reader implements `validate(resource) -> bool` and `describe(resource) -> DataDescriptor`. Selection is the same first-true rule, in entry-point name order. Readers construct with no arguments.

`describe` runs once, at staging, and returns the parts and the payload's media type. It reads headers and store metadata only, never array chunks or table rows; Tiled's adapter computes the structure at staging. How a reader finds its parts is the incarnation's business. The data server does not call readers.

A previewer implements `validate(resource) -> bool` and `preview(resource) -> DataDescriptor` for a representation of the resource. The reference server lists installed previewers in `GET /capabilities` and does not call them.

## Index

The index is the catalog the server reads. `tegel.index` provides `get(resource_id)` and `keys()`. The reference index, `tiled`, reads node keys and metadata from the sqlite catalog that the ETL application committed. `keys()` lists only children of the catalog root, so part nodes are never resources, and `get` only finds a root child. The data server's index interface is read-only. A `TiledCatalogIndex` built with no profile, for example by the `tegel.index` entry point constructed with no arguments, serves resources without `name`, `identifier`, and `date_created`; the reference server always passes the profile.

Underlying files are not modified by a read. Whether ETL copied them earlier is outside this server.

When another process writes the SQLite catalog, the reference server sees the new rows after `POST /refresh-catalog`. That route writes nothing. It is not a `DataClient` method.

## Authentication

`tegel.auth` provides a `name` and `authenticate(authorization) -> Principal`. `Principal` has a `subject` string.

The reference provider, `keycloak`, accepts RS256 bearer tokens signed by a key from the issuer's discovery document (`{issuer}/.well-known/openid-configuration`, then its `jwks_uri`), with the configured issuer and audience and a non-empty `sub`. Issuer, audience, and client id come from `TEGEL_OIDC_*`. There is no default host, realm name, or secret. Anonymous access is off.

A site replaces the provider by registering a different `tegel.auth` entry point and setting `TEGEL_AUTH` to its name. The server gives that provider to Tiled through one adapter. Keycloak is not left configured beside it. `Capabilities.auth` is the selected provider's name.

The client sends `Authorization: Bearer <token>`. `DataClient` can be given a token directly, or a token URL plus client id and client secret so it performs an OAuth2 client-credentials grant. Those values come from the caller. The client has no built-in secret and no write method.

## Versioning and compatibility

See [specs/README.md](README.md). `GET /capabilities` is the handshake. `DataClient.capabilities` raises when the server's `spec_version` major component differs from its own. Before `1.0.0`, treat a minor mismatch as a reason to stop and report the two versions.
