# tegel-core

Specification, wire contracts, and read-only data client for Tegel.

Tegel is a set of base packages that a facility extends into a modality-specific incarnation. This package holds the contracts those packages share. It does not serve data, run ETL, or know about any scientific modality.

The copyright line in `LICENSE` is a BSD-3-Clause placeholder pending confirmation with LBNL.

## Layout

- `specs/` — the specification. `specs/dataserver.md` is the data server contract.
- `src/tegel/core/` — pydantic models, plugin registry, and `DataClient`.
- `scripts/export_schemas.py` — writes JSON Schema and OpenAPI for the wire models into `specs/generated/`.

Project metadata is authored as LinkML YAML in the schema's own repository. An incarnation's search profile names that schema and one class. The generated JSON Schema describes these wire models. It is not a second way to author a resource type.

## Install

```bash
uv sync --group dev
uv run pytest
uv run ruff check src tests scripts
uv run python scripts/export_schemas.py
```

`tegel` is a namespace package. There is no `tegel/__init__.py`.
