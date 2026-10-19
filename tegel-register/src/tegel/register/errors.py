"""Error hierarchy for bind-time failures."""
from __future__ import annotations

from datetime import timedelta


class URIResolverError(Exception):
    """Base error for all tegel-register failures."""




class InvalidURIError(URIResolverError):
    """URI is malformed for scheme dispatch."""


class UnknownSchemeError(URIResolverError):
    """No source and no binder for this scheme."""


class SiteConfigError(URIResolverError):
    """Site configuration is missing or invalid."""


class PluginError(URIResolverError):
    """Plugin discovery or loading failure."""


class PluginConflictError(PluginError):
    """Two distributions registered the same protocol."""


class PluginVersionError(PluginError):
    """Binder api_version is not supported by this core."""


class SecretLookupError(URIResolverError):
    """SecretsProvider failed to retrieve a secret."""


class NamespaceResolutionError(URIResolverError):
    """Failure in namespace indirection."""


class NamespaceNotFoundError(NamespaceResolutionError):
    """Identifier unknown to the namespace authority."""


class NamespaceUnavailableError(NamespaceResolutionError):
    """Identifier exists but is not currently retrievable."""

    def __init__(self, message: str = "namespace target unavailable", *, retry_after: timedelta | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class NamespaceRestrictedError(NamespaceResolutionError):
    """Identifier exists; this caller may not have it."""


class ResolutionLoopError(NamespaceResolutionError):
    """Namespace resolution encountered a cycle or exceeded the depth limit."""


class AccessError(URIResolverError):
    """Authentication, authorization, or availability failure."""


class AuthenticationError(AccessError):
    """Caller identity not established."""


class AuthorizationError(AccessError):
    """Caller established but not permitted."""


class ResourceUnavailableError(AccessError):
    """Permitted but not currently retrievable."""

    def __init__(self, message: str = "resource unavailable", *, retry_after: timedelta | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class BindError(URIResolverError):
    """Failure planning or registering a URI."""


class ModeNotAvailableError(BindError):
    """The requested mode is not feasible for this URI."""

    def __init__(self, message: str, *, feasible: list, reasons: dict):
        super().__init__(message)
        self.feasible = feasible
        self.reasons = reasons


class NotReadableError(BindError):
    """A file:// asset is not under any readable storage entry."""


class KeyConflictError(BindError):
    """A node key already exists for a different origin."""


class DescribeError(BindError):
    """Tiled cannot detect a mimetype or structure for these bytes."""


class AcquireError(BindError):
    """Copying bytes into the landing area failed."""


class AcquireTimeoutError(AcquireError):
    """Acquisition did not finish before the timeout."""
