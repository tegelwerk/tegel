"""Names the reference server uses for the contracts in ``tegel.core``.

The contracts are defined once, in ``tegel-core``. This module re-exports them
so the server package has a single import surface.
"""

from tegel.core.plugins import AuthProvider, Extractor, Index, Previewer, Reader, Storage

__all__ = [
    "AuthProvider",
    "Extractor",
    "Index",
    "Previewer",
    "Reader",
    "Storage",
]
