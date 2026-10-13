# Tegel

Tegel is a shared base that a facility extends into a modality-specific incarnation.

This repository holds the specification and the reference servers. An incarnation is a separate installable package. It registers resource types, extractors, and readers, and it supplies a LinkML schema and a search profile. Modality vocabulary stays in the incarnation.

## Layout

- [`tegel-core`](tegel-core/README.md) — wire contracts, the plugin registry, the read-only `DataClient`, and the specification in [`tegel-core/specs`](tegel-core/specs/README.md).
- [`tegel-base`](tegel-base/README.md) — reference data server, action package, and dashboard. The data server reads an incarnation spec and launches Tiled. Keycloak is the reference authentication provider. The edge deployment is documented in [`tegel-base/deploy`](tegel-base/deploy/README.md).
- [`tegel-base/examples/toy`](tegel-base/examples/toy/README.md) — a synthetic incarnation used to exercise the reference servers.

The SSRL XFM incarnation is [tegelwerk/xfm_ssrl_tegel](https://github.com/tegelwerk/xfm_ssrl_tegel).

## Development

Each directory is its own uv project. `tegel-base` uses `tegel-core` as the path source `../tegel-core`.

```bash
cd tegel-core && uv sync --group dev && uv run pytest
cd tegel-base && uv sync && uv run pytest
```
