"""Resource types, LinkML attribute names, and search profiles."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResourceType(BaseModel):
    """A named type an incarnation registers.

    ``metadata_schema`` is a LinkML schema id. ``parent`` is an optional
    fallback type name.
    """

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    metadata_schema: str = Field(min_length=1)
    parent: str | None = None


class ProfileEntity(BaseModel):
    """Metadata keys copied onto a search-result dataset."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1)
    identifier: str = Field(min_length=1)
    date_created: str = Field(min_length=1, alias="dateCreated")


class SearchProfile(BaseModel):
    """Which schema fields are searchable and which appear in the search document."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    schema_document: str = Field(min_length=1, alias="schema")
    search: list[str]
    properties: list[str]
    entity: ProfileEntity


def linkml_attribute_names(schema: dict[str, Any]) -> list[str]:
    """Return attribute names from the tree-root class, or the only class."""
    classes = schema.get("classes") or {}
    if not isinstance(classes, dict) or not classes:
        raise ValueError("LinkML schema has no classes")
    chosen: dict[str, Any] | None = None
    for spec in classes.values():
        if isinstance(spec, dict) and spec.get("tree_root"):
            chosen = spec
            break
    if chosen is None:
        if len(classes) != 1:
            raise ValueError("LinkML schema must mark one class tree_root")
        only = next(iter(classes.values()))
        if not isinstance(only, dict):
            raise ValueError("LinkML class has no attributes")
        chosen = only
    attributes = chosen.get("attributes")
    if not isinstance(attributes, dict) or not attributes:
        raise ValueError("LinkML class has no attributes")
    return list(attributes)


def profile_unknown_slots(profile: SearchProfile, attributes: set[str]) -> list[str]:
    """Return profile slots that are not attributes of the LinkML class."""
    referenced = [
        *profile.search,
        *profile.properties,
        profile.entity.name,
        profile.entity.identifier,
        profile.entity.date_created,
    ]
    return [slot for slot in referenced if slot not in attributes]


def type_chain(types: dict[str, ResourceType], name: str) -> list[ResourceType]:
    """Walk ``parent`` links, starting at ``name``.

    A missing name raises ``KeyError``. A cycle raises ``ValueError``.
    """
    chain: list[ResourceType] = []
    seen: set[str] = set()
    current: str | None = name
    while current:
        if current in seen:
            raise ValueError(f"Resource type cycle at {current}")
        seen.add(current)
        item = types[current]
        chain.append(item)
        current = item.parent
    return chain
