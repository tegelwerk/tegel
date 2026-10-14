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
    """Which class slots are searchable and which appear in the search document.

    The profile names an external LinkML schema. ``schema_path`` is relative to
    the schema root the caller configures.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    schema_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    schema_path: str = Field(min_length=1)
    class_name: str = Field(min_length=1, alias="class")
    search: list[str]
    properties: list[str]
    entity: ProfileEntity


def class_slot_specs(schema: dict[str, Any], class_name: str) -> dict[str, dict[str, Any]]:
    """Return the slots of ``class_name`` with their definitions.

    Slots are the class's ``attributes`` and ``slots``, then the same from its
    ``is_a`` ancestors in this document. Ancestors come first. ``slot_usage``
    on a class refines the definition. Imports are not followed, so an import
    other than ``linkml:*`` is an error.
    """
    imports = schema.get("imports") or []
    if not isinstance(imports, list):
        raise ValueError("LinkML imports must be a list")
    foreign = [
        item for item in imports if not (isinstance(item, str) and item.startswith("linkml:"))
    ]
    if foreign:
        names = ", ".join(str(item) for item in foreign)
        raise ValueError(f"LinkML imports are not followed: {names}")
    classes = schema.get("classes")
    if not isinstance(classes, dict) or not classes:
        raise ValueError("LinkML schema has no classes")
    definitions = schema.get("slots") or {}
    if not isinstance(definitions, dict):
        raise ValueError("LinkML slots must be a mapping")
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: str | None = class_name
    while current:
        if current in seen:
            raise ValueError(f"LinkML is_a cycle at {current}")
        seen.add(current)
        spec = classes.get(current)
        if spec is None:
            raise ValueError(f"LinkML class {current} is not in the schema")
        if not isinstance(spec, dict):
            spec = {}
        chain.append(spec)
        parent = spec.get("is_a")
        current = parent if isinstance(parent, str) and parent else None
    found: dict[str, dict[str, Any]] = {}
    for spec in reversed(chain):
        listed = spec.get("slots") or []
        if not isinstance(listed, list):
            raise ValueError("LinkML class slots must be a list")
        for name in listed:
            definition = definitions.get(name)
            found.setdefault(str(name), dict(definition) if isinstance(definition, dict) else {})
        attributes = spec.get("attributes") or {}
        if not isinstance(attributes, dict):
            raise ValueError("LinkML class attributes must be a mapping")
        for name, definition in attributes.items():
            found.setdefault(str(name), dict(definition) if isinstance(definition, dict) else {})
        usage = spec.get("slot_usage") or {}
        if isinstance(usage, dict):
            for name, refinement in usage.items():
                if name in found and isinstance(refinement, dict):
                    found[name].update(refinement)
    if not found:
        raise ValueError(f"LinkML class {class_name} has no slots")
    return found


def linkml_attribute_names(schema: dict[str, Any], class_name: str) -> list[str]:
    """Return the slot names of ``class_name``, including ``is_a`` ancestors."""
    return list(class_slot_specs(schema, class_name))


def profile_unknown_slots(profile: SearchProfile, attributes: set[str]) -> list[str]:
    """Return profile fields that are not slots of the LinkML class."""
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
