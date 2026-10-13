# Dataserver incarnation

You implement one data-server incarnation in a new package. Fill the blanks in this document. Do not redesign the server.

The conforming skeleton is `tegel-base/examples/toy`. Read it. Copy its shape. Replace the toy record with the modality you were given.

`tegel-core` and `tegel-base` are read-only. You depend on them. You do not edit them.

## Read first

Read these, in this order, before you write code.

1. [plugins.md](../plugins.md), section "What an incarnation provides".
2. [dataserver.md](../dataserver.md), sections "Responsibilities" and "HTTP API".
3. `tegel-base/examples/toy/pyproject.toml`
4. `tegel-base/examples/toy/schema/record.yaml`
5. `tegel-base/examples/toy/schema/profile.yaml`
6. `tegel-base/examples/toy/src/tegel/toy/dataserver/__init__.py`
7. `tegel-base/examples/toy/tests/test_extractor.py`
8. `tegel-base/tests/integration/test_read_api.py`, for the `create_app`, `insert_record`, and `assert_read_only_api` sequence only.

## Writable files

Write only these files in the incarnation package. Create no other files.

- `pyproject.toml`
- `schema/<record>.yaml`
- `schema/profile.yaml`
- `src/tegel/<modality>/dataserver/__init__.py`
- `tests/test_plugin.py`
- one positive fixture under `tests/fixtures/`
- one input that `validate` must reject (a fixture or a file the test builds)

`<modality>` is a short package name for this incarnation. Do not add `src/tegel/__init__.py`. `tegel` stays a namespace package.

Runtime dependencies are `tegel-core` only. The dev group adds `tegel-dataserver`, `pytest`, and `httpx` so the checks below can run. Use the hatch layout from the toy `pyproject.toml`: wheel sources map `src` to the package root, and the wheel includes only `src/tegel`.

## Blanks

Supply these values. Leave every other contract as the reference server already implements it.

**Resource type.** One `ResourceType` named `RECORD`.

- `name` is `<modality>.<record>`, for example `toy.record`.
- `version` is `1` unless the task names another version.
- `metadata_schema` is the LinkML document `id`. The two strings are equal.

**Entry points.** Register the same entry-point name on all three groups. Point them at this module.

- `tegel.resource_types` loads `RECORD`.
- `tegel.extractors` loads the extractor class.
- `tegel.readers` loads the reader class.

Do not register `tegel.auth`, `tegel.storage`, `tegel.index`, or `tegel.previewers`.

**Schema.** One LinkML YAML document. One class, marked `tree_root`. Every name you write into catalog metadata is an attribute of that class, including:

- `plugin_id`
- `source_name`
- `key`
- `payload_uri`
- `resource_type`
- `resource_type_version`

Add the modality fields the task requires. A metadata field that is not a schema attribute is a mistake. Add the attribute, then add it to the profile only when it is searchable or copied into the RO-Crate.

**Profile.** `schema` is the schema filename beside this file. `search`, `properties`, and `entity.name`, `entity.identifier`, and `entity.dateCreated` are attributes of the tree-root class. `load_spec` rejects any other slot.

**Extractor.** Subclass `tegel.core.plugins.Extractor`. The class constructs with no arguments.

- `plugin_id` is a non-empty string and is unique among installed extractors.
- `validate(input_path: str) -> bool`. True means this plugin can read that path. False means it cannot. Return no other type.
- `process(input_path, options) -> ProcessedDataPackage`.
- `payload_path` is a readable file.
- `metadata["plugin_id"]` equals `plugin_id`.
- `metadata["source_name"]` is a non-empty string.
- Every metrics value is a float, and every metrics key is a non-empty string.
- The catalog id is `options["key"]` when that value is a non-empty string. Otherwise it is metadata `key` or `uuid`. There is no filename fallback.

**Reader.** `validate(resource) -> bool` and `describe(resource) -> DataDescriptor`.

- `validate` is true only for `RECORD.name`.
- For an array, `describe` returns `shape`, `chunks`, and `data_type` from header or store metadata.
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

## Do not copy that package's launcher

The package [tegelwerk/xfm_ssrl_tegel](https://github.com/tegelwerk/xfm_ssrl_tegel) `dataserver/` is a partial illustration. You may follow only these three ideas from it: three entry points with one name, a profile whose slots exist on the schema, and a reader that uses store metadata (`zarr.json`) instead of chunk files.

Do not copy the rest of that package. It misses this contract.

- `schema/measurement.yaml` has no `plugin_id`, `source_name`, `resource_type`, or `resource_type_version`, while `process` still writes `plugin_id` and `source_name`. The toy schema includes the fields `process` writes. A metadata field is a schema attribute.
- `tests/test_plugin.py` never calls `assert_read_only_api`. A missing token, an unknown search field, and `POST /resources` are unchecked.
- `launch.py`, `start_all.sh`, and `Containerfile` start Keycloak and build a server image. That is outside this incarnation. Do not create those files.

The `dash` and `actions` packages in that repository are out of scope.

## Done when

All of the following succeed. Use the functions named here. Do not replace them with a private check.

1. `load_spec(profile_path)` from `tegel.dataserver.spec` returns the profile. A slot that is not a schema attribute raises `ValueError`.
2. `check_package` from `tegel.core.plugins` accepts `process` on the positive fixture. `validate` on the rejected input is `False`.
3. `describe` on that package's metadata returns a `DataDescriptor`. When the family is `array`, `shape` is non-empty, and the fixture's chunk files are not opened.
4. The test builds an app with `create_app` and this profile, writes one row with `insert_record`, and calls `assert_read_only_api` from `tegel.dataserver.testing`. That check requires a bearer token (`401` without one), rejects an unknown search field (`400`), reads the resource, and rejects `POST /resources` (`405`). Follow the sequence in `tegel-base/tests/integration/test_read_api.py`.
5. From the incarnation package:

```bash
uv run pytest
uv run ruff check src tests
```

Both commands exit 0. Then stop.
