"""Read-only provenance projected from the catalog row's ``tegel`` block.

The data server does not accept a provenance write. Staging records
``plugin_id`` and ``source_name`` in the ``tegel`` block, and a read projects
them here.
"""

from pydantic import BaseModel, ConfigDict, Field


class Provenance(BaseModel):
    """How a catalog entry was produced."""

    model_config = ConfigDict(extra="ignore")

    plugin_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
