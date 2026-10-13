# Tegel specification

Tegel separates a shared base from a modality-specific incarnation.

The **base** is this specification, the wire contracts in `tegel-core`, and the reference servers in `tegel-base`. The reference servers are readable and tested. They are not a hardened production deployment. Authentication, storage, and the catalog index are interfaces. The reference data server uses Keycloak as its default authentication provider. A site can replace that provider. The reference data server reads an incarnation's spec and launches Tiled, modified to this specification, on a Tiled release that includes the entity/link graph.

An **incarnation** is a separate package that registers resource types, ETL plugins, readers, and optional previewers. It supplies a LinkML schema and a search profile. It does not modify the base. Modality vocabulary lives only in the incarnation.

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

Incarnations declare the `SPEC_VERSION` they were written against. A server and a client are compatible when their major versions match. Before `1.0.0`, the minor version may break; the reference packages in this repository move together.

## Documents

- [dataserver.md](dataserver.md) — data server
- [prompts/dataserver.md](prompts/dataserver.md) — agent procedure for one data-server incarnation
- [plugins.md](plugins.md) — entry points and what an incarnation provides
- [actions.md](actions.md) — action server, not yet specified
- [dash.md](dash.md) — dashboard, not yet specified
- [interfaces.md](interfaces.md) — YAML procedures, not yet specified
- [generated/](generated/) — JSON Schema and OpenAPI exported from the wire models
