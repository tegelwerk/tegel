# tegel-actions

Prefect flows for the catalog pipeline. The data server does not run them. The behavior is described in [`tegel-core/specs/actions.md`](../../../tegel-core/specs/actions.md).

| Command | What it does |
| --- | --- |
| `python -m tegel.actions.ingest` | Serves `ingest-directories`: claims files under `TEGEL_RECORDS` with installed extractors, stages them in batches, has the catalog updater merge each batch, then calls `POST /refresh-catalog`. |
| `python -m tegel.actions.ingest directories [records]` | Submits one run of that deployment and waits for it. |
| `python -m tegel.actions.update` | Serves `update-catalog`, the only writer of `$TEGEL_OUTPUT/catalog.db`, one run at a time. |
| `python -m tegel.actions.credentials` | Writes `$TEGEL_OUTPUT/credentials` when it is missing or incomplete. |
| `python -m tegel.actions.realm` | Creates or updates the Keycloak realm `tegel`, client `tegel`, and user `reader`. |

Staging builds every row with `catalog_metadata`, which checks the package against the slots of the class named by `TEGEL_PROFILE` under `TEGEL_SCHEMA_ROOT` and the installed resource types, and stores `{"record": ..., "tegel": ...}` under the key `resource_id_for` gives. It does not copy payload files.
