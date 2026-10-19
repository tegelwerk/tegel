"""RFC 3986 syntax helpers.

Private module. Syntax only: no I/O, no policy, no percent-decoding, no
scheme semantics. Do not grow this into a URI library.
"""
from __future__ import annotations

import re
from typing import NamedTuple

# RFC 3986 §3.1: scheme = ALPHA *( ALPHA / DIGIT / "+" / "-" / "." )
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*$")


class SplitURI(NamedTuple):
    scheme: str            # lowercased (RFC 3986 §6.2.2.1)
    raw_scheme: str        # exactly as written by the caller
    body: str              # scheme-specific-part, fragment removed, NOT decoded
    fragment: str | None   # fragment with the "#" removed, NOT decoded; None if absent


def is_valid_scheme(scheme: str) -> bool:
    """True if *scheme* matches the RFC 3986 §3.1 grammar."""
    return bool(_SCHEME_RE.match(scheme))


def split_uri(uri: str) -> SplitURI:
    """Split an absolute URI into scheme / scheme-specific-part / fragment.

    Raises ValueError for anything that is not an absolute URI with a
    syntactically valid scheme. Callers in tegel-register translate that into
    InvalidURIError.
    """
    if not isinstance(uri, str) or not uri:
        raise ValueError("URI must be a non-empty str")
    head, sep, _ = uri.partition(":")
    if not sep:
        raise ValueError(f"URI has no scheme: {uri!r}")
    if not head:
        raise ValueError(f"URI has empty scheme: {uri!r}")
    if not is_valid_scheme(head):
        raise ValueError(
            f"scheme {head!r} does not match the RFC 3986 §3.1 grammar "
            f"ALPHA *( ALPHA / DIGIT / '+' / '-' / '.' ): {uri!r}"
        )
    rest = uri[len(head) + 1 :]
    if "\\" in rest:
        raise ValueError(f"URI contains invalid '\\' character: {uri!r}")
    body, hsep, frag = rest.partition("#")
    return SplitURI(
        scheme=head.lower(),
        raw_scheme=head,
        body=body,
        fragment=frag if hsep else None,
    )


def scheme_normalized(uri: str) -> str:
    """Return *uri* with only its scheme case-normalised (RFC 3986 §6.2.2.1).

    This is NOT a general normaliser: it does not touch percent-encoding, host
    case, dot segments, or default ports. It exists so that identity comparisons
    inside tegel-register (loop detection, cache keys) do not treat 'NS:abc' and
    'ns:abc' as different URIs, which RFC 3986 §6.2.2.1 says they are not.
    """
    parts = split_uri(uri)
    return parts.scheme + ":" + uri[len(parts.raw_scheme) + 1 :]
