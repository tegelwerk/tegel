"""Load installed actions from the ``tegel.actions`` entry-point group."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from importlib.metadata import entry_points
from typing import Any, Protocol

from tegel.core.contracts.actions import ActionCard, split_ref

GROUP = "tegel.actions"


class Runnable(Protocol):
    card: ActionCard

    def run(self, context: Any, inputs: dict[str, Any], parameters: dict[str, Any]) -> dict: ...


class ActionLoadError(RuntimeError):
    """An action that cannot be served. Startup stops."""


class ActionTable:
    """Installed actions by id and version. The current version is the highest one."""

    def __init__(self, actions: Iterable[Runnable]) -> None:
        self._by_id: dict[str, dict[str, Runnable]] = {}
        for item in actions:
            card = getattr(item, "card", None)
            if not isinstance(card, ActionCard) or not callable(getattr(item, "run", None)):
                raise ActionLoadError(f"{item!r}: an action has a card (ActionCard) and run()")
            versions = self._by_id.setdefault(card.id, {})
            if card.version in versions:
                raise ActionLoadError(f"two actions are {card.ref}")
            versions[card.version] = item

    def get(self, action: str) -> Runnable:
        """The action for ``id`` (current version) or ``id@version``. ``KeyError`` if unknown."""
        action_id, version = split_ref(action)
        versions = self._by_id.get(action_id)
        if not versions:
            raise KeyError(f"unknown action {action_id!r}")
        if version is None:
            return versions[_highest(versions)]
        if version not in versions:
            raise KeyError(f"{action_id} has no version {version!r}; it has {sorted(versions)}")
        return versions[version]

    def current(self) -> list[Runnable]:
        return [self.get(action_id) for action_id in sorted(self._by_id)]

    def all(self) -> list[Runnable]:
        return [v for action_id in sorted(self._by_id) for v in self._by_id[action_id].values()]

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_id.values())


def load_actions(pattern: str = "*") -> ActionTable:
    """Every installed action whose id matches ``pattern`` (``TEGEL_ACTIONS``)."""
    found: list[Runnable] = []
    for entry in sorted(entry_points(group=GROUP), key=lambda e: e.name):
        try:
            loaded = entry.load()
        except Exception as exc:
            raise ActionLoadError(f"{GROUP}:{entry.name} failed to load: {exc}") from exc
        card = getattr(loaded, "card", None)
        if not isinstance(card, ActionCard):
            raise ActionLoadError(f"{GROUP}:{entry.name} has no ActionCard")
        if fnmatch.fnmatchcase(card.id, pattern):
            found.append(loaded)
    return ActionTable(found)


def _highest(versions: dict[str, Any]) -> str:
    try:
        from packaging.version import Version

        return max(versions, key=Version)
    except Exception:
        return max(versions)
