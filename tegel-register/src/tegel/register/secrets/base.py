"""SecretsProvider protocol (§24)."""
from __future__ import annotations
from typing import Mapping, Protocol, runtime_checkable

@runtime_checkable
class SecretsProvider(Protocol):
    def get_secret(self, secret_id: str) -> Mapping[str, str]: ...
