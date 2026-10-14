# Catalog pipeline

The reference ETL application is `tegel-actions` in `tegel-base`. It runs as Prefect flows. The data server does not run them, and the flows do not call the data server except for `POST /refresh-catalog`.

The pipeline has one writer of the production catalog. Ingest writes a new staging catalog per batch, the catalog updater merges it into production, and ingest then asks the data server to reopen the catalog.

## Ingest

`python -m tegel.actions.ingest` waits for the Prefect API and serves the flow `ingest-directories` as deployment `ingest-directories/ingest-directories`.

The flow takes `records` (default `TEGEL_RECORDS`) and `batch_size` (default 100). It:

1. walks `records` recursively in path order, skips any path with a component that starts with `.`, and keeps each file that some installed extractor validates
2. splits those files into batches
3. for each batch, writes a staging catalog at `$TEGEL_OUTPUT/staging/<random>.db`
4. runs the deployment `update-catalog/catalog-updater` with that staging path and `$TEGEL_OUTPUT/catalog.db`, and waits for it to complete
5. calls `POST /refresh-catalog`

The flow fails when `records` is not a directory, when no file is claimed, or when no record is staged. A batch that stages nothing is skipped.

`python -m tegel.actions.ingest directories [records]` submits one run of the serving deployment and waits for it. It retries for up to 180 seconds while the deployment is not registered.

## Staging

`tegel.actions.staging.write_staging(paths, staging)` creates a fresh sqlite Tiled catalog at `staging`. For each path it:

1. selects the first extractor, in entry-point name order, whose `validate` is true; a path no extractor claims is skipped
2. calls `process(path, {})`
3. builds the stored row with `catalog_metadata`, which runs `check_package` against the slots of the class the profile at `TEGEL_PROFILE` names under `TEGEL_SCHEMA_ROOT` and the installed resource types, then returns `{"record": ..., "tegel": ...}`
4. takes the catalog key from `resource_id_for(record, profile)`: the record's `entity.identifier` value, which must be one path segment
5. writes one node with structure family `bytes` and that row as its metadata

Staging never opens the production catalog. It does not copy payload files.

## Merge

`python -m tegel.actions.update` waits for the Prefect API and serves the flow `update-catalog` as deployment `update-catalog/catalog-updater`, one run at a time. The flow takes `staging_path` and `production_path` (default `$TEGEL_OUTPUT/catalog.db`).

`tegel.actions.merge.merge_staging` is the only writer of the production catalog:

- When production does not exist, the staging file is copied into place.
- Otherwise, one transaction walks staging nodes from the root and matches each one to production by parent and key. A new node is inserted with its revisions. An existing node gets the staging `metadata`, `specs`, `structure_family`, and `time_updated`, and its data sources are replaced.
- Data sources, structures, assets, and their associations are copied. An asset whose `data_uri` already exists in production is reused.
- Closure and full-text rows are left to the catalog's triggers. `alembic_version` is not copied.
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
| `TEGEL_OUTPUT` | ingest, update, credentials, realm: catalog, staging, and credentials directory |
| `TEGEL_PROFILE`, `TEGEL_SCHEMA_ROOT` | staging: profile and the schema root it resolves under |
| `PREFECT_API_URL` | ingest, update |
| `TEGEL_REFRESH_URL` | refresh: full URL of `POST /refresh-catalog` |
| `TEGEL_OIDC_ISSUER`, `TEGEL_OIDC_CLIENT_ID`, `TEGEL_OIDC_CLIENT_SECRET` | refresh |
| `PUBLIC_ORIGIN`, `PATH_DATA`, `KEYCLOAK_INTERNAL` | realm |
