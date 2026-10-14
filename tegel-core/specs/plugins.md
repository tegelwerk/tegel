# Plugins

An incarnation is an installable Python package. It depends on `tegel-core` and registers entry points. The base images and packages do not vendor that code.

Discovery uses `importlib.metadata` entry points, sorted by entry point name. That order is the deterministic order for `validate`: sort by name, then take the first `validate` that returns true. An incarnation is installed, not bind-mounted.

The reference data server reads these entry points, the search profile, and the LinkML schema that profile names under `TEGEL_SCHEMA_ROOT`, then launches Tiled in the form specified in [dataserver.md](dataserver.md). The reference ETL pipeline in [actions.md](actions.md) reads the same entry points and profile.

## Groups

| Group | Object | Used by |
| --- | --- | --- |
| `tegel.resource_types` | a `ResourceType` | data server, ETL |
| `tegel.extractors` | an extractor class | ETL application |
| `tegel.readers` | a reader class | data server |
| `tegel.previewers` | a previewer class | data server |
| `tegel.storage` | a storage class | data server, ETL |
| `tegel.auth` | an auth provider class | data server |
| `tegel.index` | an index class | data server |

Each entry point loads a class, except `tegel.resource_types`, which loads a `ResourceType` value. Classes construct with no arguments. A failure at load time is a contract error and stops startup. The server does not skip a broken plugin. An entry point in a group not listed here is not loaded.

`tegel-dataserver` registers `keycloak` on `tegel.auth`, `filesystem` on `tegel.storage`, and `tiled` on `tegel.index`.

## Extractor contract

`process` returns a `ProcessedDataPackage`:

| Field | Type | Notes |
| --- | --- | --- |
| `payload_path` | `str` | a readable file |
| `record` | `dict[str, Any]` | domain fields only; every key is a slot of the profile's class |
| `resource_type` | `str` | a registered `ResourceType.name` |
| `payload_uri` | `str \| None` | default `None`; the location `/data/content` and readers use |
| `relations` | `list[Relation]` | `{rel, target}` objects, default `[]` |
| `metrics` | `dict[str, float]` | non-empty string keys |

The extractor writes no Tegel bookkeeping. Staging takes `plugin_id` from the plugin, `source_name` from the input path's file name, and `resource_type_version` from the registered type, and stores them in the row's `tegel` block (see [dataserver.md](dataserver.md), "Stored row").

The ETL application rejects a plugin or a package that fails any of these checks. In `tegel.core.plugins`, `Registry` applies the first two when it loads `tegel.extractors`, `first_true` applies the bool check when it selects a plugin, and `check_package` applies the rest to each package. `catalog_metadata` runs `check_package` and returns the stored row.

- the class subclasses the extractor interface and constructs with no arguments
- `plugin_id` is a non-empty string and is not already used
- `validate` can be called with one input path and returns a bool
- `process` can be called with an input path and an options dict
- `process` returns a `ProcessedDataPackage`
- `payload_path` is a readable file
- `record` is a dict and every key is a slot of the profile's class
- `resource_type` is a registered resource type
- every metrics value is a float, and every metrics key is a non-empty string

`validate` answers "this plugin can read this file", not "how sure is it". The first true result in entry-point name order wins. Remaining plugins are not called.

```python
from pathlib import Path

from tegel.core.plugins import Extractor, ProcessedDataPackage


class ExamplePlugin(Extractor):
    @property
    def plugin_id(self) -> str:
        return "example-v1"

    def validate(self, input_path: str) -> bool:
        return input_path.endswith(".json")

    def process(self, input_path: str, options: dict) -> ProcessedDataPackage:
        return ProcessedDataPackage(
            payload_path=input_path,
            record={"uuid": "record-1", "title": "first"},
            resource_type="example.record",
            payload_uri=Path(input_path).resolve().as_uri(),
            metrics={},
        )
```

The catalog id is the caller's `options["key"]` when that is a non-empty string, otherwise the record's value for the profile's `entity.identifier`. There is no filename fallback.

The package also supplies a search profile. The profile names a LinkML schema by `schema_id`, `schema_version`, `schema_path`, and `class`. The schema lives in its own repository and is loaded from `TEGEL_SCHEMA_ROOT`. The incarnation does not contain it. `metadata_schema` on every `ResourceType` is that schema id; the server refuses to start otherwise. The profile's `search`, `properties`, and `entity` fields must be slots of the named class, and so must every key of `record`. The schema needs no Tegel fields. A domain field the class lacks is a change for the schema owner. Tegel does not add it.

`search` lists categorical fields only: codes, names, enumerations, and booleans. A stored boolean matches the query `true` or `false`, lowercase. A stored string matches by equality. Any other stored value never matches, so numbers, timestamps, free text, and lists go in `properties`.

## Upstream records

An extractor claims the files an upstream producer already writes: a crate, a record JSON, or a header file. It does not walk a facility tree to rebuild what that producer knows.

When an upstream producer exists, the incarnation names it. The positive fixture is a file that producer wrote from real data, committed unchanged except for redaction the human approves.

The base ingest flow (`tegel.actions.ingest`) walks `TEGEL_RECORDS` recursively and asks extractors. An incarnation's ETL action, in its `actions/` package, calls the upstream producer's library to write those files, then hands the output directory to that flow. It does not duplicate the producer's logic.

## Readers, previewers, storage, auth, index

A reader or previewer implements `validate(resource) -> bool` and either `describe(resource)` or `preview(resource)`, returning a `DataDescriptor`. The same first-true rule applies. `describe` returns chunk information for an array and does not load the array. The reference server calls readers. It lists previewers and does not call them.

A storage plugin implements `open_bytes(uri)` for a URI the catalog already stored. It does not define a copy policy.

An auth plugin has a `name` and implements `authenticate(authorization) -> Principal`. The reference registration is Keycloak. A site deployment can register a different provider and select it with `TEGEL_AUTH`. The server passes that one provider to Tiled through an adapter and does not also register Keycloak.

An index plugin implements read methods `get(resource_id)` and `keys()`. Writes belong to the ETL application.

## What an incarnation provides

1. A package that depends on `tegel-core`.
2. A profile YAML that names an external LinkML schema and class, and whose fields are slots of that class. The schema needs no Tegel fields; it is used as is.
3. A `ResourceType` entry point whose `metadata_schema` is that schema id, with an optional `parent` for fallback. Its `version` is the incarnation's mapping version, independent of `schema_version`.
4. An extractor entry point that passes the contract above.
5. A reader entry point for the payload that extractor produces.
6. Any storage, auth, or index replacement the deployment needs. The reference server already registers Keycloak.

## Worked example

`tegel-toy` is that package. The reference server loads it and launches Tiled.

- `tegel-base/examples/toy/pyproject.toml` registers `toy-record` on `tegel.resource_types`, `tegel.extractors`, and `tegel.readers`.
- `tegel-base/examples/schemas/toy/record.yaml` is the LinkML schema, outside the toy package. It holds domain slots only; `recorded` comes from the mixin `Timestamped`. `tegel-base/examples/toy/schema/profile.yaml` names it and its `Record` class, searches on `channel` and the boolean `calibrated`, and uses `uuid` as the identifier.
- `tegel-base/examples/toy/src/tegel/toy/dataserver/__init__.py` is the resource type, the extractor, and the reader. The extractor claims `.npy` files and returns the JSON sidecar next to each one as the record, with its `relations` moved to the package's `relations`. The reader returns an array descriptor from the `.npy` header at `Resource.payload_uri` and does not read the array payload. A payload that is not `.npy` is described as `bytes`.
- `tegel-base/examples/toy/tests/test_extractor.py` checks the extractor contract.
- `tegel-base/tests/integration/test_read_api.py` builds the reference server with `create_app`, writes rows built by `catalog_metadata` with `insert_record`, and runs `assert_incarnation` on the toy package. It does not write through the HTTP API.

Copy those three entry points, point the profile at the schema you were given, and keep modality vocabulary in the incarnation package. The reference process reads the installed entry points and the profile path you configure. It does not import the incarnation by name.

An agent that fills in a new incarnation follows [prompts/dataserver.md](prompts/dataserver.md). That procedure lists the writable files, the stop rules, and the checks that must pass.

Modality terms belong in the incarnation package. They do not belong in `tegel-core` or `tegel-base`.
