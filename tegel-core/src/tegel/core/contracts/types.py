"""Resource types, LinkML attribute names, and search profiles."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    @field_validator("schema_version", mode="before")
    @classmethod
    def _quoted_version(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError(
                "schema_version must be a quoted string in the profile: "
                f"got {type(value).__name__} {value!r}"
            )
        return value


def class_slot_specs(schema: dict[str, Any], class_name: str) -> dict[str, dict[str, Any]]:
    """Return the slots of ``class_name`` with their definitions.

    Classes are read in this order: ``is_a`` ancestors (recursively, ancestors
    first), then ``mixins`` in listed order (each expanded the same way), then
    the class itself. Each contributes its ``slots`` list, defined in the
    document's top-level ``slots``, then its ``attributes``. The first
    definition of a name is kept. ``slot_usage`` on each class, in the same
    order, refines a definition. A listed slot with no top-level definition,
    a missing class, and a cycle are errors. Imports are not followed, so an
    import other than ``linkml:*`` is an error.
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
    found: dict[str, dict[str, Any]] = {}
    for owner, spec in _expand(classes, class_name, []):
        listed = spec.get("slots") or []
        if not isinstance(listed, list):
            raise ValueError("LinkML class slots must be a list")
        for name in listed:
            if name not in definitions:
                raise ValueError(f"slot {name} on class {owner} is not defined in the schema")
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


def _expand(
    classes: dict[str, Any], class_name: str, path: list[str]
) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(name, spec)`` for the classes ``class_name`` reads, in slot order.

    ``path`` is the chain being expanded. A name already on it is a cycle.
    """
    if class_name in path:
        raise ValueError(f"LinkML is_a or mixins cycle at {class_name}")
    spec = classes.get(class_name)
    if spec is None:
        raise ValueError(f"LinkML class {class_name} is not in the schema")
    if not isinstance(spec, dict):
        spec = {}
    inner = [*path, class_name]
    ordered: list[tuple[str, dict[str, Any]]] = []
    parent = spec.get("is_a")
    if isinstance(parent, str) and parent:
        ordered.extend(_expand(classes, parent, inner))
    mixins = spec.get("mixins") or []
    if not isinstance(mixins, list):
        raise ValueError(f"LinkML mixins on class {class_name} must be a list")
    for mixin in mixins:
        ordered.extend(_expand(classes, str(mixin), inner))
    ordered.append((class_name, spec))
    return ordered


def linkml_attribute_names(schema: dict[str, Any], class_name: str) -> list[str]:
    """Return the slot names of ``class_name``, including ancestors and mixins."""
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
