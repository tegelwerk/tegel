# Tegel specification

Tegel does three things. It loads a LinkML schema it is given, maps records that an upstream producer wrote into a Tiled catalog, and serves them read-only. It does not author schemas, and it does not walk, parse or reorganize source data. Those belong to the schema's repository and to the producer.

Tegel separates a shared base from a modality-specific incarnation.

The **base** is this specification, the wire contracts in `tegel-core`, and the reference servers in `tegel-base`. The reference servers are readable and tested. They are not a hardened production deployment. Authentication, storage, and the catalog index are interfaces. The reference data server uses Keycloak as its default authentication provider. A site can replace that provider. The reference data server reads an incarnation's spec and launches Tiled, modified to this specification, on a Tiled release that includes the entity/link graph.

An **incarnation** is a separate package that registers resource types, ETL plugins, readers, and optional previewers. It supplies a search profile that names a LinkML schema and one class in it. It does not contain that schema. It does not modify the base. Modality vocabulary lives only in the incarnation.

`tegel-core` is the piece other institutes can implement against. `tegel-base` is one implementation. A facility may replace the reference server and still pass the same contracts.

## Versioning

The specification uses semantic versioning. `SPEC_VERSION` in `tegel.core.version` is that version. A server reports it from `GET /capabilities`.

A **breaking** change is any of:

- removing an HTTP route, or changing what a successful response means
- changing the RO-Crate shape used when `search_document` is `ro-crate`
- changing plugin selection from "the first plugin whose `validate` returns true"
- removing or renaming a required field on `Capabilities`, `Resource`, or `SearchProfile`
- requiring a new query parameter

A **compatible** change is any of:

- adding an optional field
- adding an HTTP route
- advertising a new `search_document` value besides `ro-crate`
- adding an entry-point group

The current version is `0.2.0`. Incarnations declare the `SPEC_VERSION` they were written against. A server and a client are compatible when their major versions match. Before `1.0.0`, the minor version may break; the reference packages in this repository move together.

## Documents

- [dataserver.md](dataserver.md) — data server: read API, resource model, profiles, configuration
- [plugins.md](plugins.md) — entry points and what an incarnation provides
- [actions.md](actions.md) — reference catalog pipeline: staging, merge, refresh
- [prompts/dataserver.md](prompts/dataserver.md) — agent procedure for one data-server incarnation
- [dash.md](dash.md) — dashboard
- [interfaces.md](interfaces.md) — YAML procedures
- [generated/](generated/) — JSON Schema and OpenAPI exported from the wire models by `scripts/export_schemas.py`
