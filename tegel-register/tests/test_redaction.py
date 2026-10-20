"""Redaction of messages and opaque URI payloads."""

from __future__ import annotations

from tegel.register.errors import URIResolverError
from tegel.register.redaction import redact_message, sanitize_exception


def test_sanitize_exception_strips_credentials_by_default():
    class BackendError(Exception):
        pass

    exc = BackendError("failed Authorization: Bearer SUPERSECRET download")
    wrapped = sanitize_exception(exc, wrapper_type=URIResolverError, retain_raw=False)
    assert "SUPERSECRET" not in str(wrapped)
    assert wrapped.__cause__ is None
    assert wrapped.__context__ is None


def test_sanitize_exception_retain_raw():
    class BackendError(Exception):
        pass

    exc = BackendError("token=VISIBLE")
    wrapped = sanitize_exception(exc, wrapper_type=URIResolverError, retain_raw=True)
    assert wrapped.__cause__ is exc


def test_opaque_payload_in_message():
    uri = "private:v1:OPAQUE_PAYLOAD_SECRET"
    text = redact_message(f"error resolving {uri}", opaque_uris=[uri])
    assert "OPAQUE_PAYLOAD_SECRET" not in text
    assert "sha256:" in text
