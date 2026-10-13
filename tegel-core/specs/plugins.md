# Plugins

An incarnation is an installable Python package. It depends on `tegel-core` and registers entry points. The base images and packages do not vendor that code.

Discovery uses `importlib.metadata` entry points, sorted by entry point name. That order is the deterministic order for `validate`. ProductionLambda scanned a plugin directory by filename; Tegel uses entry points so an incarnation can be installed rather than bind-mounted. The selection rule is the same: sort by name, then take the first `validate` that returns true.

The reference data server reads these entry points, the LinkML schema, and the search profile, then launches Tiled in the form specified in [dataserver.md](dataserver.md). The Tiled release must include the entity/link graph (0.2.15 or any later release that still ships it).

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

Each entry point loads a class, except `tegel.resource_types`, which loads a `ResourceType` value. Classes construct with no arguments. A failure at load time is a contract error and stops startup. The server does not skip a broken plugin.

## Extractor contract

The ETL application loads `tegel.extractors` and rejects a plugin that fails any of these checks:

- the class subclasses the extractor interface and constructs with no arguments
- `plugin_id` is a non-empty string and is not already used
- `validate` can be called with one input path and returns a bool
- `process` can be called with an input path and an options dict
- `process` returns a `ProcessedDataPackage`
- `payload_path` is a readable file
- `metadata["plugin_id"]` equals the plugin id
- `metadata["source_name"]` is a non-empty string
- every metrics value is a float, and every metrics key is a non-empty string

`validate` answers "this plugin can read this file", not "how sure is it". The first true result in entry-point name order wins. Remaining plugins are not called.

```python
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
            metadata={"plugin_id": self.plugin_id, "source_name": "record.json", "uuid": "record-1"},
            metrics={},
        )
```

The catalog id is `options["key"]` when that is a non-empty string, otherwise metadata `key` or `uuid`. There is no filename fallback.

The package also supplies a LinkML schema and a search profile. `metadata_schema` on the `ResourceType` is the schema id. The profile's `search`, `properties`, and `entity` slots must be attributes of that schema's tree-root class. Adding a metadata field is a schema edit, then a profile edit if the field should be searchable or copied into the RO-Crate.

## Readers, previewers, storage, auth, index

A reader or previewer implements `validate(resource) -> bool` and either `describe(resource)` or `preview(resource)`, returning a `DataDescriptor`. The same first-true rule applies. `describe` returns chunk information for an array and does not load the array.

A storage plugin opens bytes for a URI the catalog already stored. It does not define a copy policy.

An auth plugin implements `authenticate(authorization) -> Principal`. The reference registration is Keycloak. A site deployment can register a different provider. The server passes that one provider to Tiled through an adapter and does not also register Keycloak.

An index plugin implements read methods `get(resource_id)` and `keys()`. Writes belong to the ETL application.

## What an incarnation provides

1. A package that depends on `tegel-core`.
2. A LinkML schema and a profile YAML whose slots are schema attributes.
3. A `ResourceType` entry point whose `metadata_schema` is that schema id, with an optional `parent` for fallback.
4. An extractor entry point that passes the contract above.
5. A reader entry point for the payload that extractor produces.
6. Any storage, auth, or index replacement the deployment needs. The reference server already registers Keycloak.

## Worked example

`tegel-toy` is that package. The reference server loads it and launches Tiled.

- `tegel-base/examples/toy/pyproject.toml` registers `toy-record` on `tegel.resource_types`, `tegel.extractors`, and `tegel.readers`.
- `tegel-base/examples/toy/schema/record.yaml` is the LinkML schema. `profile.yaml` beside it selects `channel` for search and for the RO-Crate properties.
- `tegel-base/examples/toy/src/tegel/toy/dataserver/__init__.py` is the extractor and the reader. The reader returns an array descriptor from the `.npy` header and does not read the array payload. A payload that is not `.npy` is described as `bytes`.
- `tegel-base/examples/toy/tests/test_extractor.py` checks the extractor contract.
- `tegel-base/tests/integration/test_read_api.py` starts the reference server and runs the conformance kit: search, get, open data. It does not write through the HTTP API.

Copy those three entry points, point the profile at your schema, and keep modality vocabulary in the incarnation package. The reference process reads the installed entry points and the profile path you configure. It does not import the incarnation by name.

An agent that fills in a new incarnation follows [prompts/dataserver.md](prompts/dataserver.md). That procedure lists the writable files, the stop rules, and the checks that must pass.

Modality terms belong in the incarnation package. They do not belong in `tegel-core` or `tegel-base`.
