"""Redaction replaces an opaque URI payload with the scheme and a digest."""
from __future__ import annotations
import hashlib
import re
from typing import Iterable

_OPAQUE_SCHEMES: set[str] = set()
_SENSITIVE = (
    re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|secret)\s*[:=]\s*\S+"),
    re.compile(r"(?i)authorization\s*:\s*\S+(?:\s+\S+)*"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-+/=]+"),
    re.compile(r"(?i)(aws_secret_access_key|private[_-]?key)\s*[:=]\s*\S+"),
)

def extract_scheme(uri: str) -> str:
    from tegel.register._uriparse import split_uri
    return split_uri(uri).scheme

def register_opaque_scheme(scheme: str) -> None:
    _OPAQUE_SCHEMES.add(scheme.lower())

def is_opaque_scheme(scheme: str) -> bool:
    return scheme.lower() in _OPAQUE_SCHEMES

def redact_opaque_payload(scheme: str, payload: str) -> str:
    digest = hashlib.sha256(payload.encode("utf-8", errors="surrogateescape")).hexdigest()[:12]
    return f"{scheme}:<sha256:{digest}>"

def redact_uri(uri: str, *, opaque: bool | None = None) -> str:
    if not uri or ":" not in uri:
        return "<invalid-uri>"
    scheme, payload = uri.split(":", 1)
    if opaque is None:
        opaque = is_opaque_scheme(scheme)
    if opaque:
        return redact_opaque_payload(scheme, payload)
    return uri

def redact_message(message: str, *, opaque_uris: Iterable[str] = ()) -> str:
    """Replace secret-shaped text and listed opaque URI payloads in a message."""
    text = message
    for uri in opaque_uris:
        if uri and ":" in uri:
            scheme, payload = uri.split(":", 1)
            text = text.replace(uri, redact_opaque_payload(scheme, payload))
    for pattern in _SENSITIVE:
        def _replace(m: re.Match[str], _pattern: re.Pattern[str] = pattern) -> str:
            if m.lastindex:
                return f"{m.group(1)}=<redacted>"
            return "<redacted>"

        text = pattern.sub(_replace, text)
    text = re.sub(r"(?i)(https?://[^/\s]+:)([^@/\s]+)(@)", r"\1<redacted>\3", text)
    return text

def sanitize_exception(
    exc: BaseException,
    *,
    wrapper_type: type[Exception],
    message: str | None = None,
    retain_raw: bool = False,
    opaque_uris: Iterable[str] = (),
) -> Exception:
    raw = message if message is not None else str(exc)
    wrapped = wrapper_type(redact_message(raw, opaque_uris=opaque_uris))
    if retain_raw:
        wrapped.__cause__ = exc
    else:
        wrapped.__cause__ = None
        wrapped.__suppress_context__ = True
    return wrapped
