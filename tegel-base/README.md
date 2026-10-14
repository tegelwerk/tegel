# tegel-base

Reference implementation of Tegel on `tegel-core`. It is readable and tested. It is not a hardened production deployment.

BSD-3-Clause. Copyright is held by The Tegelwerk Contributors; see `LICENSE`.

## Packages

This directory is a uv workspace. `tegel-core` is the editable path source `../tegel-core`.

- [`packages/dataserver`](packages/dataserver/README.md) — `tegel-dataserver`. Loads an incarnation's entry points, search profile, and external LinkML schema, and serves the read API on Tiled (`tiled[server]>=0.2.15`, with `tiled.graph`). Registers the reference `keycloak` auth, `filesystem` storage, and `tiled` index plugins. Ships the conformance kit `tegel.dataserver.testing`.
- [`packages/actions`](packages/actions/README.md) — `tegel-actions`. Prefect flows that stage records through installed extractors, merge each staging catalog into production, and call `POST /refresh-catalog`. Also the credential and Keycloak realm setup used by the deployment.
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

The root `conftest.py` sets `TEGEL_SCHEMA_ROOT` to `examples/schemas` for every test. `tests/integration/test_read_api.py` builds the server with `create_app`, writes catalog rows with `insert_record` (no HTTP route calls it), and runs `assert_incarnation` on the toy package.
