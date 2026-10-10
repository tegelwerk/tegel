# tegel-base

Reference implementation of the Tegel servers. It is readable and tested. It is not a hardened production deployment.

`tegel-dataserver` reads an incarnation spec and launches Tiled, modified to the data-server specification, on a Tiled release that includes the entity/link graph (`tiled[server]>=0.2.15`, and any later release that still ships `tiled.graph`). Keycloak is the reference authentication provider. The action server and dashboard packages are placeholders.

Copyright in `LICENSE` is held by "The Tegelwerk Contributors" until the copyright holder is confirmed.

`tegel-core` is the path source `../tegel-core`.

```bash
uv sync
uv run pytest
uv run ruff check packages examples tests
uv run lint-imports
```

The data server does not run ETL. Catalog rows are written by a downstream application. Tests insert rows through `insert_record`, which no HTTP route calls.
