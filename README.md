# Tegel

Tegel loads a LinkML schema it is given, maps records that an upstream producer wrote into a Tiled catalog, and serves that catalog read-only over HTTP.

This repository holds the specification, the shared contracts, and the reference implementation. A facility extends it with an incarnation: a separate installable package that registers resource types, extractors, and readers, and supplies a search profile that names an external LinkML schema and class. Modality vocabulary stays in the incarnation.

Each catalog row stores the domain record, keyed by slots of the schema's class, apart from Tegel's own bookkeeping (plugin, source, resource type, payload location, relations, schema id and version). The schema needs no Tegel fields. The specification version is `0.3.2`.

## Layout

- [`tegel-core`](tegel-core/README.md) — the specification in [`tegel-core/specs`](tegel-core/specs/README.md), pydantic wire contracts, the plugin registry and checks, the profile and schema loader, and the read-only `DataClient`.
- [`tegel-base`](tegel-base/README.md) — the reference data server (Tiled with the Tegel read API and Keycloak auth), the Prefect catalog pipeline, an empty dashboard package, and the container deployment in [`tegel-base/deploy`](tegel-base/deploy/README.md).
- [`tegel-base/examples/toy`](tegel-base/examples/toy/README.md) — a synthetic incarnation that the tests run the reference server against. Its schema is in `tegel-base/examples/schemas`.

The SSRL XFM incarnation is [tegelwerk/xfm_ssrl_tegel](https://github.com/tegelwerk/xfm_ssrl_tegel).

## Development

Each directory is its own uv project. `tegel-base` uses `../tegel-core` as an editable path source.

```bash
cd tegel-core && uv sync --group dev && uv run pytest
cd tegel-base && uv sync && uv run pytest
```
