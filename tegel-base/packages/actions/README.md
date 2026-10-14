# tegel-actions

Prefect flows for the catalog pipeline. The data server does not run them. The behavior is described in [`tegel-core/specs/actions.md`](../../../tegel-core/specs/actions.md).

| Command | What it does |
| --- | --- |
| `python -m tegel.actions.ingest` | Serves `ingest-directories`: claims files under `TEGEL_RECORDS` with installed extractors, stages them in batches, has the catalog updater merge each batch, then calls `POST /refresh-catalog`. |
| `python -m tegel.actions.ingest directories [records] [--on-error skip\|fail]` | Submits one run of that deployment and waits for it. |
| `python -m tegel.actions.errors [--run RUN_ID \| --last] [--stage STAGE] [--json]` | Shows lines from `$TEGEL_OUTPUT/ingest-errors.jsonl` (or `--file PATH`) as a table, or unchanged with `--json`. |
| `python -m tegel.actions.update` | Serves `update-catalog`, the only writer of `$TEGEL_OUTPUT/catalog.db`, one run at a time. |
| `python -m tegel.actions.credentials` | Writes `$TEGEL_OUTPUT/credentials` when it is missing or incomplete. |
| `python -m tegel.actions.realm` | Creates or updates the Keycloak realm `tegel`, client `tegel`, and user `reader`. |

Staging builds every row with `catalog_metadata`, which checks the package against the slots of the class named by `TEGEL_PROFILE` under `TEGEL_SCHEMA_ROOT` and the installed resource types, and stores `{"record": ..., "tegel": ...}` under the key `resource_id_for` gives. It does not copy payload files.

A record that fails `process`, the package check, the id rule, the duplicate check, or the write is a bad record. In `skip` mode (the default; `TEGEL_ON_ERROR` or the flow's `on_error`) it is logged and staging continues. In `fail` mode it is logged, the batch's staging catalog is deleted, and the run fails. Each bad record is one line in `$TEGEL_OUTPUT/ingest-errors.jsonl`. A missing resource type or a schema mismatch fails the run in either mode.
