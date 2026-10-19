# Catalog pipeline

The reference ETL application is `tegel-actions` in `tegel-base`. It runs as Prefect flows. The data server does not run them, and the flows do not call the data server except for `POST /refresh-catalog`.

The pipeline has one writer of the production catalog. Ingest writes a new staging catalog per batch, the catalog updater merges it into production, and ingest then asks the data server to reopen the catalog.

## Ingest

`python -m tegel.actions.ingest` waits for the Prefect API and serves the flow `ingest-directories` as deployment `ingest-directories/ingest-directories`.

The flow takes `records` (default `TEGEL_RECORDS`), `batch_size` (default 100), and `on_error` (see [Errors](#errors)). It:

1. resolves the error mode and fails on an invalid value before claiming any path
2. walks `records` recursively in path order, skips any path with a component that starts with `.`, and keeps each file that some installed extractor validates
3. splits those files into batches
4. for each batch, writes a staging catalog at `$TEGEL_OUTPUT/staging/<random>.db`, passing the mode, `$TEGEL_OUTPUT/ingest-errors.jsonl`, one run id shared by every batch, and the readable roots (`TEGEL_READABLE_STORAGE`, else `TEGEL_RECORDS`, else `records`)
5. runs the deployment `update-catalog/catalog-updater` with that staging path and `$TEGEL_OUTPUT/catalog.db`, and waits for it to complete
6. calls `POST /refresh-catalog`

The flow fails when `records` is not a directory, when no file is claimed (`no record in <root>`), or when no record is staged across the whole run (`no record staged from <root>: <M> skipped; errors in <log>`). A batch that stages nothing is not merged, and its staging files are removed. Batches merged before a failure stay merged. Each run prints one summary, `staged N, skipped M`, followed by `; errors in <path>` when M is above zero.

`python -m tegel.actions.ingest directories [records] [--on-error skip|fail]` submits one run of the serving deployment and waits for it. It retries for up to 180 seconds while the deployment is not registered. Without `--on-error`, it passes `TEGEL_ON_ERROR` when that is set, and otherwise leaves the mode to the worker.

## Staging

`tegel.actions.staging.write_staging(paths, staging, *, on_error="skip", error_log=None, run_id=None, roots=None, upstreams=None, upstream_client=None)` returns a `StagingResult` with `staged`, `skipped`, and `errors` (one `StagingError` per bad record, with `path`, `stage`, `error`, and `message`).

First it loads the profile at `TEGEL_PROFILE` under `TEGEL_SCHEMA_ROOT` and its class slots, and checks with `schema_mismatch` that resource types are installed and all name the profile's `schema_id`. It loads the readers in entry-point name order, the readable roots (`roots`, default `TEGEL_READABLE_STORAGE`, else `TEGEL_RECORDS`) and the upstreams (`upstreams`, default the file at `TEGEL_UPSTREAMS`). Then it creates a fresh sqlite Tiled catalog at `staging`, with those roots as Tiled's readable storage. For each path it runs these stages in order. A failure at any stage makes the record bad (see [Errors](#errors)):

| Stage | Step |
| --- | --- |
| | select the first extractor, in entry-point name order, whose `validate` is true; a path no extractor claims is passed over and not counted |
| `process` | call `process(path, {})` |
| `check` | build the stored row `{"record": ..., "tegel": ...}` with `catalog_metadata`, which runs `check_package` against the class slots and the installed resource types |
| `parts` | describe the data parts once and plan who serves each (see [Parts](#parts)); store `parts` and `payload` in the `tegel` block |
| `id` | take the catalog key from `resource_id_for(record, profile)`: the record's `entity.identifier` value, one path segment |
| `duplicate` | reject a key already staged in this batch, before anything is written |
| `write` | write the resource as a `container` node with the row as its metadata, then one externally managed child per Tiled part (a child that cannot be created is a `parts` error) |

### Parts

`tegel.actions.parts` is the module that builds Tiled adapters and data sources. For each record:

1. Staging builds the `Resource` the server would see from the row and calls `describe_parts` (`tegel.core.plugins`): the first reader whose `validate` is true, then its `describe`. The resource's id is provisional at this stage: the record's identifier value, or the source file's stem when there is none, with uri `urn:tegel:staging/<id>`. No reader, a reader that raises, and an invalid `DataDescriptor` (including a 0.3 descriptor with top-level `family`) are `parts` errors.
2. A `file:` part must lie under a readable root; otherwise the message names the path and the roots.
3. A part whose `media_type` Tiled registers from one URI (see [dataserver.md](dataserver.md#who-serves-a-part)) is built with Tiled's registration adapter, `adapter_cls.from_uris(asset_uri, dataset=...)`. The adapter's family must equal the part's `family`, and a `shape` or `columns` hint must equal the adapter's structure, with both values in the message. Missing `shape`, `chunks`, `data_type` and `columns` are filled from the structure. The part is stored with `served_by: "tiled"`. CSV and Excel adapters read the whole file here; Zarr, HDF5, npy and TIFF read headers only.
4. Every other `file:` part is stored with `served_by: "tegel"` and its `size`.
5. A remote part (`remote` set, http(s) `asset_uri`) must name an origin listed in `TEGEL_UPSTREAMS`, and its path must start with that upstream's `api_prefix` followed by `/metadata/`; otherwise the message names the URL. Staging sends `GET asset_uri` with the upstream's credentials and a timeout of `TEGEL_UPSTREAM_TIMEOUT` seconds (default 10). The response's `structure_family` must equal the part's `family`, and the `shape` and `columns` hints must match the remote structure. An HTTP error or a timeout is a `parts` error. The part is stored with `served_by` set to its `remote` value, `upstream` (the origin) and `remote_path` (the node path after `/metadata/`).
6. The payload is a byte part named `payload` with `asset_uri` set to `payload_uri`, the media type of the reader's `payload` (or `application/octet-stream` when the reader gives none), and `served_by: "tegel"`.

Staging never opens the production catalog. It does not copy payload files. It logs on the `tegel.staging` logger. When any exception escapes the stages, `write_staging` closes the staging catalog, removes its file and the `-wal` and `-shm` files, and re-raises.

## Errors

A bad record is an exception raised for one claimed path at the `process`, `check` (`PluginContractError` or `ValueError`), `parts` (`PluginContractError` or `ValueError`), `id` (`ValueError`), `duplicate`, or `write` stage. What happens next depends on the mode:

| Mode | On a bad record |
| --- | --- |
| `skip` (default) | write one error-log line, log a warning, continue with the next record |
| `fail` | write one error-log line, log an error, delete the batch's staging catalog, and raise `StagingError` naming the path and the reason; nothing from that batch is merged |

The flow's mode is its `on_error` argument, else `TEGEL_ON_ERROR`, else `skip`. Any value other than `skip` or `fail` fails the run, naming the variable and the allowed values.

These always fail the run, in either mode, and write no error-log line: the profile or schema does not load, `schema_mismatch` reports a problem, a reader entry point does not construct, `TEGEL_READABLE_STORAGE` or `TEGEL_UPSTREAMS` is invalid, or the staging catalog cannot be created.

The error log is one append-only JSON Lines file, `$TEGEL_OUTPUT/ingest-errors.jsonl`. A caller of `write_staging` that passes `error_log=None` gets the logger only. Each bad record is one line with exactly these keys:

```json
{"time": "2026-10-14T18:25:03Z", "run_id": "8f3c...", "mode": "skip", "stage": "check",
 "path": "/records/c1d2/record.json", "plugin_id": "example-v1",
 "resource_id": null, "error": "PluginContractError",
 "message": "example-v1: record keys are not class slots: exposure_count"}
```

- `time` is UTC, ISO 8601 with seconds and a `Z` suffix.
- `run_id` is the Prefect flow run id, or a random hex id outside a flow run. It is the same for every batch in one run.
- `plugin_id` is `null` when no plugin was selected.
- `resource_id` is set only for the `duplicate` and `write` stages, and for a `parts` error raised while writing Tiled children.
- `error` is the exception's class name.
- `message` is the exception's text on one line: newlines become spaces, and text over 2000 characters is cut and ends with `…`.

The file holds bad records only. Each line is written with one `write` call and flushed, so concurrent runs do not interleave lines.

`python -m tegel.actions.errors [--file PATH] [--run RUN_ID | --last] [--stage STAGE] [--json]` reads `$TEGEL_OUTPUT/ingest-errors.jsonl`, or `--file`. It prints a table of time, stage, path, and message. `--last` keeps only the most recent run id, and `--json` prints the matching lines unchanged. It prints `no errors` and exits 0 when nothing matches or the file is missing. It never deletes or rotates the file; rotation is the operator's job.

## Merge

`python -m tegel.actions.update` waits for the Prefect API and serves the flow `update-catalog` as deployment `update-catalog/catalog-updater`, one run at a time. The flow takes `staging_path` and `production_path` (default `$TEGEL_OUTPUT/catalog.db`).

`tegel.actions.merge.merge_staging` is the only writer of the production catalog:

- When production does not exist, the staging file is copied into place.
- Otherwise, one transaction walks staging nodes from the root and matches each one to production by parent and key, so part children merge like resources. A new node is inserted with its revisions. An existing node gets the staging `metadata`, `specs`, `structure_family`, and `time_updated`, and its data sources are replaced.
- When a staging node replaces a production node, production children that staging does not have are deleted with their descendants, data sources, data-source–asset links, revisions, and closure rows. Assets and structures that no remaining data source uses are deleted at the end of the same transaction; an asset another resource still uses is kept.
- Data sources, structures, assets, and their associations are copied. An asset whose `data_uri` already exists in production is reused.
- Closure and full-text rows for inserted nodes are left to the catalog's triggers. `alembic_version` is not copied.
- Any error rolls the transaction back. On success the staging file is removed.

## Refresh

`tegel.actions.refresh.refresh_catalog` gets a client-credentials token from `{TEGEL_OIDC_ISSUER}/protocol/openid-connect/token` with `TEGEL_OIDC_CLIENT_ID` and `TEGEL_OIDC_CLIENT_SECRET`, posts to `TEGEL_REFRESH_URL`, and fails unless the body is `{"refreshed": true}`.

## Deployment helpers

`python -m tegel.actions.credentials` writes `$TEGEL_OUTPUT/credentials` with `admin_password`, `client_secret`, `user_password`, and `session_secret`, mode `0600`. A file that already has all four values is left alone.

`python -m tegel.actions.realm` reads that file, logs in to Keycloak at `KEYCLOAK_INTERNAL` as `admin`, and creates or updates:

- the realm `tegel`
- the confidential client `tegel` with the stored client secret, standard flow, direct access grants, service accounts, the redirect URI `{PUBLIC_ORIGIN}/{PATH_DATA}/*`, the web origin `PUBLIC_ORIGIN`, and an audience mapper for `tegel`
- the user `reader` with the stored user password

## Environment

| Variable | Used by |
| --- | --- |
| `TEGEL_RECORDS` | ingest: directory to walk |
| `TEGEL_OUTPUT` | ingest, update, credentials, realm, errors: catalog, staging, error log, and credentials directory |
| `TEGEL_ON_ERROR` | ingest: `skip` (default) or `fail` |
| `TEGEL_PROFILE`, `TEGEL_SCHEMA_ROOT` | staging: profile and the schema root it resolves under |
| `TEGEL_READABLE_STORAGE` | staging: absolute directories, separated by `os.pathsep`, that file parts must lie under; default `TEGEL_RECORDS` |
| `TEGEL_UPSTREAMS`, `TEGEL_UPSTREAM_TIMEOUT` | staging: upstream Tiled servers for remote parts, and the timeout in seconds (default 10); the secrets the file names |
| `PREFECT_API_URL` | ingest, update |
| `TEGEL_REFRESH_URL` | refresh: full URL of `POST /refresh-catalog` |
| `TEGEL_OIDC_ISSUER`, `TEGEL_OIDC_CLIENT_ID`, `TEGEL_OIDC_CLIENT_SECRET` | refresh |
| `PUBLIC_ORIGIN`, `PATH_DATA`, `KEYCLOAK_INTERNAL` | realm |
