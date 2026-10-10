"""Read-only provenance projected from catalog metadata.

The data server does not accept a provenance write. ETL records ``plugin_id``
and ``source_name`` on the catalog entry, and a read projects them here.
"""

from pydantic import BaseModel, ConfigDict, Field


class Provenance(BaseModel):
    """How a catalog entry was produced."""

    model_config = ConfigDict(extra="ignore")

    plugin_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
