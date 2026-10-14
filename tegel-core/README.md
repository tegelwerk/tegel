# tegel-core

Specification, wire contracts, plugin checks, and read-only data client for Tegel. Other implementations can build against this package without `tegel-base`.

It does not serve data, run ETL, depend on Tiled, or know about any scientific modality. Its runtime dependencies are `pydantic`, `httpx`, and `pyyaml`.

BSD-3-Clause. Copyright is held by The Tegelwerk Contributors; see `LICENSE`.

## Layout

- `specs/` — the specification. Start at [`specs/README.md`](specs/README.md).
- `src/tegel/core/contracts/` — pydantic wire models (`Capabilities`, `Resource`, `ResourcePage`, `DataPart`, `DataDescriptor`, `SearchResult`, `SearchProfile`, `ResourceType`, `Provenance`), the stored-row envelope `CatalogEnvelope` with `split_catalog_metadata`, `resource_from_catalog`, and `resource_id_for`, search matching, and the RO-Crate search document builder and parser.
- `src/tegel/core/plugins.py` — the entry-point groups, `Registry`, the `Extractor` base class and `ProcessedDataPackage`, the reader, previewer, storage, auth, and index protocols, `first_true`, `check_package`, `catalog_metadata`, which builds the stored `{"record", "tegel"}` row, `readers_in_order` and `describe_parts`, which pick a reader and validate its parts, and `with_parts`, which stores them in the row.
- `src/tegel/core/spec.py` — `load_spec(profile_path, schema_root)`, which loads a search profile and the LinkML schema it names under `TEGEL_SCHEMA_ROOT`, `record_slots`, which returns the slots of the profile's class, and `readable_storage_from_env` for `TEGEL_READABLE_STORAGE`.
- `src/tegel/core/upstreams.py` — the `TEGEL_UPSTREAMS` file model for remote parts, `load_upstreams`, `split_remote`, and `UpstreamAuth`, an httpx auth for API keys and cached client-credentials tokens. Secrets come from environment variables only.
- `src/tegel/core/client/` — `DataClient`: capabilities, search, browse, get, metadata, and data parts: `data`, `parts`, `read_array` (raw bytes in an `ArrayBytes` with the part's shape and dtype), `read_table`, and `read_bytes` with byte ranges. They follow each part's `content_url` with Tiled's query parameters; the Tiled Python client works against the same URLs. Bearer token, or OAuth2 client credentials; the token is only sent to the client's own origin, and `upstream_auth` gives credentials for link parts on other origins. No write method.
- `src/tegel/core/version.py` — `SPEC_VERSION`.
- `scripts/export_schemas.py` — writes JSON Schema and OpenAPI for the wire models into `specs/generated/`.

LinkML schemas live in their own repositories. An incarnation's search profile names one schema and one class. The generated JSON Schema describes the wire models. It is not a way to author a resource type.

## Develop

```bash
uv sync --group dev
uv run pytest
uv run ruff check src tests scripts
uv run python scripts/export_schemas.py
```

`tegel` is a namespace package. There is no `tegel/__init__.py`.
