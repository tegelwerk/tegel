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

Staging builds every row with `catalog_metadata`, which checks the package against the slots of the class named by `TEGEL_PROFILE` under `TEGEL_SCHEMA_ROOT` and the installed resource types. It then asks the first matching reader for the record's data parts, once, and `tegel.actions.register` plans them: parts Tiled can read are registered as externally managed children with the structure Tiled's adapter computes, other parts are served by Tegel as bytes, and remote parts are checked against their upstream (`TEGEL_UPSTREAMS`). The resource is stored as a `container` node with `{"record": ..., "tegel": ...}` under the key `resource_id_for` gives. File parts must lie under `TEGEL_READABLE_STORAGE` (default `TEGEL_RECORDS`). It does not copy payload files.

The merge deletes part children that a re-ingested resource no longer has, with their data sources and any asset no other resource uses, in the same transaction.

A record that fails `process`, the package check, the `parts` stage, the id rule, the duplicate check, or the write is a bad record. In `skip` mode (the default; `TEGEL_ON_ERROR` or the flow's `on_error`) it is logged and staging continues. In `fail` mode it is logged, the batch's staging catalog is deleted, and the run fails. Each bad record is one line in `$TEGEL_OUTPUT/ingest-errors.jsonl`. A missing resource type or a schema mismatch fails the run in either mode.
