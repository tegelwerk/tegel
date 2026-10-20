"""Destage phase: store a finished run as a resource in the results catalog.

Outputs are written as files under ``outputs/<run id>/``. Arrays and tables are
registered as externally managed Tiled children (via tegel-register's
``describe_local``, the same description the data server pipeline uses), so they
are sliced on Tiled's routes. Everything else is a byte part served by Tegel.
The RO-Crate is the payload. The row is ``{"record": ..., "tegel": ...}``, so the
data server read API serves it unchanged.
"""

from __future__ import annotations

import json
import mimetypes
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tegel.actionserver.authoring import Quantity
from tegel.actionserver.catalogs import Catalogs
from tegel.actionserver.config import ActionServerConfig
from tegel.core.contracts.actions import ActionCard, PortKind
from tegel.core.contracts.resources import (
    ENVELOPE_KEY,
    RECORD_KEY,
    CatalogEnvelope,
    DataPart,
    Relation,
    StructureFamily,
    dump_envelope,
    resource_uri,
)
from tegel.core.contracts.types import ResourceType, SearchProfile

RUN_TYPE = ResourceType(
    name="tegel.run", version="1", metadata_schema="https://w3id.org/tegel/actionserver/run"
)
CRATE_MEDIA_TYPE = "application/ld+json"
TERMS = "https://w3id.org/tegel/actionserver/terms#"


class DestageError(RuntimeError):
    """Outputs that do not match the card, or a write the catalog refused."""


@dataclass
class RunFacts:
    """What destage needs to know about the run, besides its outputs."""

    run_id: str
    card: ActionCard
    principal: str
    inputs: dict[str, Any]
    parameters: dict[str, Any]
    request_key: str
    submitted: str
    started: str | None
    ended: str
    state: str
    error: str | None = None
    software: dict[str, str] = field(default_factory=dict)


@dataclass
class Written:
    parts: list[DataPart]
    tiled: dict[str, tuple[str, tuple[dict, ...]]]  # part -> (family, data sources)
    quantities: dict[str, dict[str, Any]]


class Destager:
    def __init__(self, config: ActionServerConfig, catalogs: Catalogs, profile: SearchProfile,
                 server_version: str) -> None:
        self.config = config
        self.catalogs = catalogs
        self.profile = profile
        self.server_version = server_version

    def store(self, facts: RunFacts, outputs: dict[str, Any] | None) -> str:
        """Write the run resource and return its URI. A failed run passes ``outputs=None``."""
        directory = self.config.outputs_dir / facts.run_id
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
        written = Written([], {}, {})
        if outputs is not None:
            written = self._write_outputs(facts.card, outputs, directory)
        uri = resource_uri(self.config.public_origin, facts.run_id)
        crate = build_crate(facts, written, uri=uri, origin=self.config.public_origin,
                            server_version=self.server_version)
        crate_path = directory / "ro-crate-metadata.json"
        crate_path.write_text(json.dumps(crate, indent=2), encoding="utf-8")
        payload = DataPart(
            name="payload", family=StructureFamily.bytes, media_type=CRATE_MEDIA_TYPE,
            asset_uri=crate_path.as_uri(), served_by="tegel", size=crate_path.stat().st_size,
        )
        row = self._row(facts, written, payload)
        self._write(facts.run_id, row, written)
        return uri

    # -- outputs --------------------------------------------------------------------------------

    def _write_outputs(self, card: ActionCard, outputs: dict[str, Any], directory: Path) -> Written:
        if not isinstance(outputs, dict):
            raise DestageError(f"{card.ref} returned {type(outputs).__name__}, not a dict")
        declared = {p.name: p for p in card.outputs}
        unknown = sorted(set(outputs) - set(declared))
        if unknown:
            raise DestageError(f"{card.ref} returned undeclared outputs {unknown}")
        missing = [p.name for p in card.outputs
                   if not p.optional and outputs.get(p.name) is None]
        if missing:
            raise DestageError(f"{card.ref} did not return outputs {missing}")
        written = Written([], {}, {})
        for name, port in declared.items():
            value = outputs.get(name)
            if value is None:
                continue
            try:
                self._one(port.kind, name, value, port.unit, directory, written)
            except DestageError:
                raise
            except Exception as exc:
                raise DestageError(f"output {name!r} ({port.kind.value}): {exc}") from exc
        return written

    def _one(self, kind: PortKind, name: str, value: Any, unit: str | None, directory: Path,
             written: Written) -> None:
        if kind is PortKind.array:
            import numpy as np

            path = directory / f"{name}.npy"
            np.save(path, np.asarray(value), allow_pickle=False)
            self._tiled_part(name, path, StructureFamily.array, written)
        elif kind is PortKind.table:
            import pandas as pd

            frame = value if isinstance(value, pd.DataFrame) else pd.DataFrame(value)
            frame.columns = [str(c) for c in frame.columns]
            path = directory / f"{name}.parquet"
            frame.to_parquet(path, index=False)
            self._tiled_part(name, path, StructureFamily.table, written)
        elif kind is PortKind.quantity:
            quantity = value if isinstance(value, Quantity) else Quantity(float(value))
            if quantity.unit is None:
                quantity.unit = unit
            record = _jsonable(quantity.as_record())
            if not _finite(record["numeric_value"]):
                raise DestageError(f"quantity {name!r} is not a finite number")
            written.quantities[name] = record
            self._json_part(name, record, directory, written)
        elif kind is PortKind.json:
            self._json_part(name, _jsonable(value), directory, written)
        elif kind is PortKind.structure and not isinstance(value, (str, Path)):
            data = value.as_dict() if hasattr(value, "as_dict") else value
            self._json_part(name, _jsonable(data), directory, written)
        else:  # file, structure or trajectory given as a path
            source = Path(value)
            if not source.is_file():
                raise DestageError(f"output {name!r} is not a file: {source}")
            path = directory / f"{name}{''.join(source.suffixes)}"
            shutil.copy2(source, path)
            media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            written.parts.append(DataPart(
                name=name, family=StructureFamily.bytes, media_type=media,
                asset_uri=path.as_uri(), served_by="tegel", size=path.stat().st_size,
            ))

    def _json_part(self, name: str, value: Any, directory: Path, written: Written) -> None:
        path = directory / f"{name}.json"
        path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
        written.parts.append(DataPart(
            name=name, family=StructureFamily.bytes, media_type="application/json",
            asset_uri=path.as_uri(), served_by="tegel", size=path.stat().st_size,
        ))

    def _tiled_part(self, name: str, path: Path, family: StructureFamily,
                    written: Written) -> None:
        from tegel.register.tiled.describe import describe_local

        description = describe_local(path, server_uri=path.as_uri(), is_directory=False)
        if description is None or description.structure_family != family.value:
            raise DestageError(f"output {name!r}: Tiled cannot register {path.name}")
        hints: dict[str, Any] = {}
        structure = (description.data_sources[0] or {}).get("structure") or {}
        if family is StructureFamily.array:
            hints["shape"] = [int(n) for n in structure.get("shape") or []]
        else:
            hints["columns"] = [str(c) for c in structure.get("columns") or []]
        written.parts.append(DataPart(
            name=name, family=family, media_type=description.mimetype,
            asset_uri=path.as_uri(), served_by="tiled", size=path.stat().st_size, **hints,
        ))
        written.tiled[name] = (description.structure_family, description.data_sources)

    # -- row and write --------------------------------------------------------------------------

    def _row(self, facts: RunFacts, written: Written, payload: DataPart) -> dict[str, Any]:
        record: dict[str, Any] = {
            "run_id": facts.run_id,
            "action": facts.card.id,
            "action_version": facts.card.version,
            "action_ref": facts.card.ref,
            "state": facts.state,
            "principal": facts.principal,
            "submitted": facts.submitted,
            "started": facts.started,
            "ended": facts.ended,
            "request_key": facts.request_key,
            "error": facts.error,
            "inputs": facts.inputs,
            "parameters": _jsonable(facts.parameters),
            "quantities": written.quantities,
            "outputs": [p.name for p in written.parts],
        }
        relations = [Relation(rel="used", target=uri) for uri in _input_uris(facts.inputs)]
        envelope = CatalogEnvelope(
            plugin_id=facts.card.ref,
            source_name=facts.run_id,
            resource_type=RUN_TYPE.name,
            resource_type_version=RUN_TYPE.version,
            payload_uri=payload.asset_uri,
            relations=relations,
            schema_id=self.profile.schema_id,
            schema_version=self.profile.schema_version,
            class_name=self.profile.class_name,
            parts=written.parts,
            payload=payload,
        )
        return {RECORD_KEY: record, ENVELOPE_KEY: dump_envelope(envelope)}

    def _write(self, run_id: str, row: dict[str, Any], written: Written) -> None:
        from tegel.register.tiled.apply import data_source

        with self.catalogs.results_lock:
            results = self.catalogs.results
            if run_id in results:
                results.delete_contents([run_id], recursive=True, external_only=True)
            container = results.create_container(key=run_id, metadata=row)
            try:
                for name, (family, sources) in written.tiled.items():
                    container.new(family, [data_source(s) for s in sources], key=name,
                                  metadata={})
            except Exception as exc:
                results.delete_contents([run_id], recursive=True, external_only=True)
                raise DestageError(f"Tiled refused an output node: {exc}") from exc


def build_crate(facts: RunFacts, written: Written, *, uri: str, origin: str,
                server_version: str) -> dict[str, Any]:
    """The run's RO-Crate, with PROV-O provenance (specs/actionserver.md, Results)."""
    card = facts.card
    plan_id = f"{origin}/actions/{card.id}#{card.version}"
    agent_id = f"{origin}/#action-server"
    person_id = f"urn:tegel:principal:{facts.principal}"
    graph: list[dict[str, Any]] = [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
         "about": {"@id": "./"}, "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"}},
        {"@id": "./", "@type": "Dataset", "name": f"{card.ref} run {facts.run_id}",
         "datePublished": facts.ended, "mainEntity": {"@id": uri},
         "hasPart": [{"@id": p.name} for p in written.parts]},
    ]
    activity: dict[str, Any] = {
        "@id": uri,
        "@type": "prov:Activity",
        "name": card.title or card.id,
        "prov:startedAtTime": facts.started,
        "prov:endedAtTime": facts.ended,
        "prov:used": [{"@id": u} for u in _input_uris(facts.inputs)],
        "prov:wasAssociatedWith": {"@id": agent_id},
        "prov:hadPlan": {"@id": plan_id},
        "tegel:state": facts.state,
        "tegel:requestKey": facts.request_key,
        "tegel:parameters": json.dumps(_jsonable(facts.parameters), sort_keys=True),
    }
    if facts.error:
        activity["tegel:error"] = facts.error
    produced = {p.name for p in written.parts}
    absent = [p.name for p in card.outputs if p.name not in produced]
    if absent:
        activity["tegel:notProduced"] = absent
    graph.append(activity)
    graph.append({"@id": plan_id, "@type": ["prov:Plan", "SoftwareApplication"],
                  "name": card.id, "version": card.version, "description": card.description,
                  **({"license": card.license} if card.license else {})})
    graph.append({"@id": agent_id, "@type": "prov:SoftwareAgent",
                  "name": "tegel-actionserver", "version": server_version,
                  "prov:actedOnBehalfOf": {"@id": person_id},
                  **({"tegel:software": facts.software} if facts.software else {})})
    graph.append({"@id": person_id, "@type": "prov:Agent", "identifier": facts.principal})
    for port, value in facts.inputs.items():
        for u in value if isinstance(value, list) else [value]:
            graph.append({"@id": u, "@type": "prov:Entity", "tegel:port": port})
    for part in written.parts:
        entity: dict[str, Any] = {
            "@id": part.name, "@type": ["File", "prov:Entity"],
            "encodingFormat": part.media_type, "contentSize": part.size,
            "tegel:port": part.name, "prov:wasGeneratedBy": {"@id": uri},
        }
        if part.name in written.quantities:
            entity["tegel:quantity"] = written.quantities[part.name]
        graph.append(entity)
    return {
        "@context": ["https://w3id.org/ro/crate/1.1/context",
                     {"prov": "http://www.w3.org/ns/prov#", "tegel": TERMS}],
        "@graph": graph,
    }


def _input_uris(inputs: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for value in inputs.values():
        found.extend(value if isinstance(value, list) else [value])
    return found


def _finite(value: Any) -> bool:
    import math

    return isinstance(value, (int, float)) and math.isfinite(value)


def _jsonable(value: Any) -> Any:
    import math

    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item") and callable(value.item) and getattr(value, "ndim", 1) == 0:
        value = value.item()
    elif hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
