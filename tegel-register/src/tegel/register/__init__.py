"""tegel-register public exports.

A bare import loads none of tiled, globus_sdk, numpy, pandas, or pyarrow.
Names are resolved on first access.
"""
from __future__ import annotations

__version__ = "0.1.0"

_CONTEXT = {"BindContext", "Context", "NamespaceConfig"}
_BIND = {
    "Acquisition",
    "Binding",
    "BinderPlan",
    "Mode",
    "NodeSpec",
    "OnConflict",
    "Origin",
    "Plan",
    "plan",
    "register",
}
_ERRORS = {
    "AccessError",
    "AcquireError",
    "AcquireTimeoutError",
    "AuthenticationError",
    "AuthorizationError",
    "BindError",
    "DescribeError",
    "InvalidURIError",
    "KeyConflictError",
    "ModeNotAvailableError",
    "NamespaceNotFoundError",
    "NamespaceResolutionError",
    "NamespaceRestrictedError",
    "NamespaceUnavailableError",
    "NotReadableError",
    "PluginConflictError",
    "PluginError",
    "PluginVersionError",
    "ResolutionLoopError",
    "ResourceUnavailableError",
    "SecretLookupError",
    "SiteConfigError",
    "URIResolverError",
    "UnknownSchemeError",
}

__all__ = ["__version__", "Site", *_CONTEXT, *_BIND, *_ERRORS]


def __getattr__(name: str):
    if name in _CONTEXT:
        from tegel.register.context import BindContext, NamespaceConfig

        values = {
            "BindContext": BindContext,
            "Context": BindContext,
            "NamespaceConfig": NamespaceConfig,
        }
        return values[name]
    if name in _BIND:
        from tegel.register import bind as bind_mod

        return getattr(bind_mod, name)
    if name in _ERRORS:
        from tegel.register import errors as errors_mod

        return getattr(errors_mod, name)
    if name == "Site":
        from tegel.register.site import Site

        return Site
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
