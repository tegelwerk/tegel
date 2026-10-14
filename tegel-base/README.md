# tegel-base

Reference implementation of Tegel on `tegel-core`. It is readable and tested. It is not a hardened production deployment.

BSD-3-Clause. Copyright is held by The Tegelwerk Contributors; see `LICENSE`.

## Packages

This directory is a uv workspace. `tegel-core` is the editable path source `../tegel-core`.

- [`packages/dataserver`](packages/dataserver/README.md) — `tegel-dataserver`. Loads an incarnation's entry points, search profile, and external LinkML schema, and serves the read API on Tiled (`tiled[server,formats]>=0.2.18`, with `tiled.graph`), including data parts with content URLs, HTTP Range for byte parts, and a streaming proxy for remote parts. Registers the reference `keycloak` auth, `filesystem` storage, and `tiled` index plugins. Ships the conformance kit `tegel.dataserver.testing`.
- [`packages/actions`](packages/actions/README.md) — `tegel-actions`. Prefect flows that stage records through installed extractors and readers, register Tiled-readable data parts as Tiled nodes, merge each staging catalog into production, and call `POST /refresh-catalog`. Also the credential and Keycloak realm setup used by the deployment.
- [`packages/dash`](packages/dash/README.md) — `tegel-dash`. An empty package.
- [`examples/toy`](examples/toy/README.md) — `tegel-toy`, the reference incarnation. Its LinkML schema is in `examples/schemas/toy/record.yaml`, outside the package.
- [`deploy`](deploy/README.md) — Caddy, Keycloak, Prefect, the data server, and the pipeline workers under one public origin.

The import contracts in `.importlinter` keep the three packages on `tegel.core`, independent of each other, and free of imports from `tegel.toy`. `tests/test_vocabulary.py` keeps modality terms out of `packages/`.

## Develop

```bash
uv sync
uv run pytest
uv run ruff check packages examples tests
uv run lint-imports
```

The root `conftest.py` sets `TEGEL_SCHEMA_ROOT` to `examples/schemas` for every test. The dev group adds `h5py`, `pandas`, `pyarrow`, `tifffile` and `zarr` for the tests.

- `tests/integration/test_read_api.py` stages toy records with `write_staging`, merges them with `merge_staging`, builds the server with `create_app`, reads arrays, table columns and byte ranges through each part's `content_url`, and runs `assert_incarnation` on the toy package.
- `tests/integration/test_remote_parts.py` serves link and proxy parts against a second, plain Tiled app.
- `tests/integration/test_merge_parts.py` covers the index's root-only keys and the merge's stale-child delete.
- `packages/actions/tests/test_tiled_registration_spike.py` pins which formats Tiled 0.2.18 registers from a URI.
