"""Resources, search filters, and the reference RO-Crate search document."""

from enum import StrEnum
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from tegel.core.contracts.provenance import Provenance
from tegel.core.contracts.types import SearchProfile

RO_CRATE = "https://w3id.org/ro/crate/1.1"
RO_CRATE_CONTEXT = "https://w3id.org/ro/crate/1.1/context"


class StructureFamily(StrEnum):
    """Tiled structure families a reader may return."""

    array = "array"
    table = "table"
    container = "container"
    sparse = "sparse"
    bytes = "bytes"


class Relation(BaseModel):
    """A directed link stored by ETL and returned on read."""

    model_config = ConfigDict(extra="ignore")

    rel: str = Field(min_length=1)
    target: str = Field(min_length=1)


class ResourceRef(BaseModel):
    """A catalog entry in a browse listing."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    type: str | None = None


class Resource(BaseModel):
    """One catalog record served by the data server."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    type: str | None = None
    type_version: str | None = None
    name: str | None = None
    identifier: str | None = None
    date_created: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    relations: list[Relation] = Field(default_factory=list)
    provenance: Provenance | None = None


class SearchQuery(BaseModel):
    """Exact-match filters. Keys must be fields on the active profile."""

    model_config = ConfigDict(extra="ignore")

    filters: dict[str, str] = Field(default_factory=dict)


class SearchHit(BaseModel):
    """One match, independent of the on-the-wire document."""

    model_config = ConfigDict(extra="ignore")

    uri: str = Field(min_length=1)
    name: str | None = None
    identifier: str | None = None
    date_created: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    """Parsed search response.

    ``document`` is ``ro-crate`` for the reference representation, or another
    token advertised by ``Capabilities.search_document``.
    """

    model_config = ConfigDict(extra="ignore")

    document: str = Field(min_length=1)
    count: int = Field(ge=0)
    hits: list[SearchHit] = Field(default_factory=list)


class DataDescriptor(BaseModel):
    """How to read a resource without implying the bytes are loaded."""

    model_config = ConfigDict(extra="ignore")

    family: StructureFamily
    media_type: str = Field(min_length=1)
    content_url: str | None = None
    shape: list[int] | None = None
    chunks: list[list[int]] | None = None
    data_type: str | None = None


class ResourcePage(BaseModel):
    """Body of ``GET /resources``."""

    model_config = ConfigDict(extra="ignore")

    resources: list[ResourceRef] = Field(default_factory=list)
    count: int = Field(ge=0)


class ErrorBody(BaseModel):
    """Error body used by the HTTP API."""

    model_config = ConfigDict(extra="ignore")

    detail: str


def resource_uri(public_origin: str, resource_id: str) -> str:
    """Build the resource URL placed in an RO-Crate."""
    origin = public_origin.rstrip("/")
    if not origin:
        raise ValueError("public_origin is required")
    return f"{origin}/resources/{quote(resource_id, safe='')}"


def resource_id_from_metadata(
    metadata: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> str:
    """Return the explicit catalog id.

    Order is caller ``options["key"]``, then metadata ``key``, then metadata
    ``uuid``. A missing id is an error. Filenames are not used.
    """
    options = options or {}
    candidates: list[Any] = [options.get("key")]
    candidates.append(metadata.get("key"))
    candidates.append(metadata.get("uuid"))
    for value in candidates:
        if isinstance(value, str) and value.strip() and value == value.strip():
            if "/" in value or "\\" in value:
                raise ValueError("resource id must be a single path segment")
            return value
    raise ValueError("resource id must be an explicit key or uuid")


def relations_from_metadata(metadata: dict[str, Any]) -> list[Relation]:
    """Project ``metadata["relations"]`` when it is a list of rel/target objects."""
    raw = metadata.get("relations")
    if not isinstance(raw, list):
        return []
    found: list[Relation] = []
    for item in raw:
        if (
            isinstance(item, dict)
            and isinstance(item.get("rel"), str)
            and item["rel"]
            and isinstance(item.get("target"), str)
            and item["target"]
        ):
            found.append(Relation(rel=item["rel"], target=item["target"]))
    return found


def provenance_from_metadata(metadata: dict[str, Any]) -> Provenance | None:
    """Project plugin id and source name when both are non-empty strings."""
    plugin_id = metadata.get("plugin_id")
    source_name = metadata.get("source_name")
    if (
        isinstance(plugin_id, str)
        and plugin_id
        and isinstance(source_name, str)
        and source_name
    ):
        return Provenance(plugin_id=plugin_id, source_name=source_name)
    return None


def resource_from_catalog(
    resource_id: str,
    uri: str,
    metadata: dict[str, Any],
    *,
    type_name: str | None = None,
    type_version: str | None = None,
    profile: SearchProfile | None = None,
) -> Resource:
    """Build a resource view from a catalog metadata object."""
    name = None
    identifier = None
    date_created = None
    if profile is not None:
        hit = hit_from_metadata(uri, metadata, profile, fallback_id=resource_id)
        name = hit.name
        identifier = hit.identifier
        date_created = hit.date_created
    return Resource(
        id=resource_id,
        uri=uri,
        type=type_name,
        type_version=type_version,
        name=name,
        identifier=identifier,
        date_created=date_created,
        metadata=dict(metadata),
        relations=relations_from_metadata(metadata),
        provenance=provenance_from_metadata(metadata),
    )


def unknown_filters(filters: dict[str, str], profile: SearchProfile) -> list[str]:
    """Return filter names that the profile does not list under ``search``."""
    return [key for key in filters if key not in profile.search]


def metadata_matches(metadata: dict[str, Any], filters: dict[str, str]) -> bool:
    """True when every filter equals the stored metadata value."""
    for field, expected in filters.items():
        if metadata.get(field) != expected:
            return False
    return True


def hit_from_metadata(
    uri: str,
    metadata: dict[str, Any],
    profile: SearchProfile,
    *,
    fallback_id: str,
) -> SearchHit:
    """Copy profile entity fields and properties from catalog metadata."""

    def _text(field: str) -> str | None:
        value = metadata.get(field)
        if value is None:
            return None
        return str(value)

    name = _text(profile.entity.name) or fallback_id
    identifier = _text(profile.entity.identifier) or fallback_id
    return SearchHit(
        uri=uri,
        name=name,
        identifier=identifier,
        date_created=_text(profile.entity.date_created),
        properties={prop: metadata.get(prop) for prop in profile.properties},
    )


def build_search_crate(
    hits: list[SearchHit],
    profile: SearchProfile,
    public_origin: str,
) -> dict[str, Any]:
    """Build the reference RO-Crate search document."""
    origin = public_origin.rstrip("/")
    if not origin:
        raise ValueError("public_origin is required")
    parts: list[dict[str, str]] = []
    datasets: list[dict[str, Any]] = []
    for hit in hits:
        parts.append({"@id": hit.uri})
        datasets.append(
            {
                "@id": hit.uri,
                "@type": "Dataset",
                "name": hit.name,
                "identifier": hit.identifier,
                "dateCreated": hit.date_created,
                "contentUrl": hit.uri,
                "additionalProperty": [
                    {"@type": "PropertyValue", "name": prop, "value": hit.properties.get(prop)}
                    for prop in profile.properties
                ],
            }
        )
    return {
        "@context": RO_CRATE_CONTEXT,
        "@graph": [
            {
                "@id": "ro-crate-metadata.json",
                "@type": "CreativeWork",
                "about": {"@id": "./"},
                "conformsTo": [{"@id": RO_CRATE}, {"@id": profile.id}],
            },
            {
                "@id": "./",
                "@type": "Dataset",
                "name": profile.name,
                "hasPart": parts,
            },
            *datasets,
        ],
    }


def _graph(document: dict[str, Any]) -> list[dict[str, Any]]:
    graph = document.get("@graph")
    if not isinstance(graph, list):
        return []
    return [node for node in graph if isinstance(node, dict)]


def parse_search_crate(document: dict[str, Any]) -> SearchResult:
    """Parse a reference RO-Crate search document into hits."""
    hits: list[SearchHit] = []
    for node in _graph(document):
        if node.get("@type") != "Dataset":
            continue
        node_id = node.get("@id")
        if node_id in {"./", "ro-crate-metadata.json"}:
            continue
        if not isinstance(node_id, str) or not node_id:
            continue
        properties: dict[str, Any] = {}
        extra = node.get("additionalProperty") or []
        if isinstance(extra, list):
            for item in extra:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    properties[item["name"]] = item.get("value")
        content = node.get("contentUrl")
        uri = content if isinstance(content, str) and content else node_id
        date_created = node.get("dateCreated")
        name = node.get("name")
        identifier = node.get("identifier")
        hits.append(
            SearchHit(
                uri=uri,
                name=name if isinstance(name, str) else None,
                identifier=identifier if isinstance(identifier, str) else None,
                date_created=str(date_created) if date_created is not None else None,
                properties=properties,
            )
        )
    return SearchResult(document="ro-crate", count=len(hits), hits=hits)


def parse_search_body(content_type: str, payload: Any) -> SearchResult:
    """Parse either the reference RO-Crate or a JSON ``SearchResult``."""
    if not isinstance(payload, dict):
        raise ValueError("Search response must be a JSON object")
    media = content_type.split(";", 1)[0].strip().lower()
    if media == "application/ld+json" or "@graph" in payload:
        return parse_search_crate(payload)
    result = SearchResult.model_validate(payload)
    return result
