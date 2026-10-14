# Dataserver incarnation

You implement one data-server incarnation in a new package. Fill the blanks in this document. Do not redesign the server.

The conforming skeleton is `tegel-base/examples/toy`. Read it. Copy its shape. Replace the toy record with the modality you were given.

`tegel-core` and `tegel-base` are read-only. You depend on them. You do not edit them.

## Required inputs

Before you write code, the human supplies:

- the name of the upstream producer, if one exists
- one real positive fixture that producer wrote, or a directory skeleton captured from real data

If neither a real fixture nor a real skeleton is present, stop and ask. Do not synthesize a layout, a file format, or field values.

## Read first

Read these, in this order, before you write code.

1. [plugins.md](../plugins.md), section "What an incarnation provides".
2. [dataserver.md](../dataserver.md), sections "Responsibilities" and "HTTP API".
3. `tegel-base/examples/toy/pyproject.toml`
4. `tegel-base/examples/schemas/toy/record.yaml`, the external schema the toy profile names
5. `tegel-base/examples/toy/schema/profile.yaml`
6. `tegel-base/examples/toy/src/tegel/toy/dataserver/__init__.py`
7. `tegel-base/examples/toy/tests/test_extractor.py`
8. `tegel-base/tests/integration/test_read_api.py`, for the `create_app`, `insert_record`, and `assert_incarnation` sequence only.

## Writable files

Write only these files in the incarnation package. Create no other files.

- `pyproject.toml`
- `schema/profile.yaml`
- `src/tegel/<modality>/dataserver/__init__.py`
- `tests/test_plugin.py`
- one positive fixture under `tests/fixtures/`
- one input that `validate` must reject (a fixture or a file the test builds)

`<modality>` is a short package name for this incarnation. Do not add `src/tegel/__init__.py`. `tegel` stays a namespace package.

Runtime dependencies are `tegel-core` plus, at most, the format library the reader needs. A reader may depend on one format library (for example `h5py`) when `describe` must read header metadata. `describe` still does not read chunk bytes. The dev group adds `tegel-dataserver`, `pytest`, and `httpx` so the checks below can run. Use the hatch layout from the toy `pyproject.toml`: wheel sources map `src` to the package root, and the wheel includes only `src/tegel`.

## Actions package

An incarnation repository is a uv workspace with members `dataserver/`, `actions/`, and `dash/`. This procedure covers `dataserver/` only. That package walks no tree and runs no ETL.

ETL belongs in the `actions/` package. That package calls the upstream producer's library (for MX, `lambda-rocrate` from `etldatadash`) and then the base `tegel.actions.staging` / `tegel.actions.ingest` code. An actions package re-implements no discovery the upstream library already provides.

## Blanks

Supply these values. Leave every other contract as the reference server already implements it.

**Resource type.** One `ResourceType` named `RECORD`.

- `name` is `<modality>.<record>`, for example `toy.record`.
- `version` is `1` unless the task names another version.
- `metadata_schema` is the LinkML document `id` and the profile's `schema_id`. The strings are equal; the server refuses to start otherwise.

**Entry points.** Register the same entry-point name on all three groups. Point them at this module.

- `tegel.resource_types` loads `RECORD`.
- `tegel.extractors` loads the extractor class.
- `tegel.readers` loads the reader class.

Do not register `tegel.auth`, `tegel.storage`, `tegel.index`, or `tegel.previewers`.

**Schema.** The human supplies `schema_id`, `schema_version`, `schema_path` and `class`. Do not write or edit a schema. A domain field you need that the class lacks is a stop: report it for the schema owner.

Return domain fields in `record`, the payload location in `payload_uri`, links in `relations`. Do not write `plugin_id`, `source_name`, or version fields; Tegel adds them. Every `record` key is a slot of that class.

**Profile.** Copy the four schema values into the profile as `schema_id`, `schema_version` (a quoted string), `schema_path` (relative to `TEGEL_SCHEMA_ROOT`), and `class`. `search`, `properties`, and `entity.name`, `entity.identifier`, and `entity.dateCreated` are slots of that class. `load_spec` rejects any other field. `entity.identifier` is the record field that gives the catalog id. `search` lists categorical fields only: codes, names, enumerations, and booleans. A stored boolean matches the query `true` or `false`; a stored string matches by equality; nothing else matches. Numbers, timestamps, free text, and lists go in `properties`.

**Extractor.** Subclass `tegel.core.plugins.Extractor`. The class constructs with no arguments.

- `plugin_id` is a non-empty string and is unique among installed extractors.
- `validate(input_path: str) -> bool`. True means this plugin can read that path. False means it cannot. Return no other type.
- `process(input_path, options) -> ProcessedDataPackage(payload_path, record, resource_type, payload_uri, relations, metrics)`.
- `payload_path` is a readable file.
- `record` holds domain fields only, each a slot of the class.
- `resource_type` is `RECORD.name`.
- `payload_uri` is the payload's URI, or `None` when there is no payload.
- `relations` is a list of `{rel, target}` objects, default `[]`.
- Every metrics value is a float, and every metrics key is a non-empty string.
- The catalog id is the caller's `options["key"]` when that value is a non-empty string. Otherwise it is `record[entity.identifier]`. There is no filename fallback.

**Reader.** `validate(resource) -> bool` and `describe(resource) -> DataDescriptor`.

- `validate` is true only for `RECORD.name` (`resource.type`).
- `describe` reads the payload at `resource.payload_uri`. `resource.metadata` is the record block.
- For an array, `describe` returns `shape`, `chunks`, and `data_type` from header or store metadata. One format library may read that header.
- `describe` does not open chunk files and does not load the array payload.
- A payload that is not an array is `StructureFamily.bytes` with media type `application/octet-stream`.

## Stop

Stop, and do not edit further, when the next change would do any of the following.

- Modify a file in `tegel-core` or `tegel-base`. That includes routes, `create_app`, Tiled setup, Keycloak, catalog merge, and `SPEC_VERSION`.
- Add an HTTP route, a second server process, a container build, or a write path. The data server is read-only. A test may call `insert_record`. Catalog writes in production stay in the ETL application.
- Register an auth, storage, or index plugin. The reference providers stay: Keycloak, filesystem, and the Tiled catalog.
- Put a modality word into `tegel-core` or `tegel-base`. Modality vocabulary stays in this package.
- Import this package by name from the base. The server loads installed entry points and the profile path it is configured with.
- Make `validate` return a score. The first entry point whose `validate` returns true wins. Entry points are sorted by name. Later plugins are not called.
- Read array chunk bytes inside `describe`.
- Invent a facility path layout, file format, or naming convention that is not in the supplied fixture.
- Re-implement discovery or record building that the upstream producer already does.
- Write, copy, or edit a LinkML schema, or need a field the supplied class lacks. Report the field for the schema owner.
- Create a launcher, a start script, or a `Containerfile`, or start Keycloak. The base deployment in `tegel-base/deploy` does that.

Copy the shape of the toy package only. Do not copy another incarnation's files. A reader may use store metadata (for example `zarr.json`) in place of chunk files.

## Done when

Call `assert_incarnation` from `tegel.dataserver.testing` on this `dataserver/` package. Do not replace it with a private check. A passing `pytest` run of hand-written assertions is not a substitute.

`assert_incarnation` checks the package layout, that the package contains no LinkML schema, runtime and dev dependencies, entry points, the profile and its external schema, that the resource type's `metadata_schema` equals the profile's `schema_id`, search-field ranges, the extractor on the positive fixture and the rejected input, the stored row `catalog_metadata` builds, `describe`, and the read API. Build the app with `create_app`, write one row built by `catalog_metadata` with `insert_record`, and pass that server to `assert_incarnation`. Follow the sequence in `tegel-base/tests/integration/test_read_api.py`.

From the incarnation package, with `TEGEL_SCHEMA_ROOT` set to the directory the human named:

```bash
uv run pytest
uv run ruff check src tests
```

Both commands exit 0. Then stop.
