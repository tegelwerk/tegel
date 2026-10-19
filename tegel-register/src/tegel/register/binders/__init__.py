"""Binder protocol.

Importing this package does not import a binder implementation or Tiled.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from tegel.register.binders._registry import BinderRegistry, get_global_registry, register_binder

if TYPE_CHECKING:
    from tegel.register.bind import Acquisition, BinderPlan, Mode
    from tegel.register.context import BindContext
    from tegel.register.site import Source

__all__ = [
    "Binder",
    "BinderRegistry",
    "get_global_registry",
    "register_binder",
]


class Binder(Protocol):
    """A plugin that plans a bind for one protocol."""

    api_version: int
    protocol: str

    def validate_source(self, source: Source) -> None:
        """Raise SiteConfigError when source params are invalid."""

    def feasible_modes(
        self, uri: str, source: Source | None, into: Any, ctx: BindContext
    ) -> list[tuple[Mode, str | None]]:
        """Every Mode, with None when feasible and a short reason otherwise."""

    def plan(
        self,
        uri: str,
        source: Source | None,
        into: Any,
        ctx: BindContext,
        *,
        mode: Mode,
        key: str | None,
    ) -> BinderPlan:
        """Build the binder-owned fields of a plan. No writes."""

    def acquire(
        self, acquisition: Acquisition, ctx: BindContext, *, timeout: float | None
    ) -> Path:
        """Place bytes at the landing path. Atomic: temp sibling, then rename."""
