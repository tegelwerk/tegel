"""Namespace resolution and cache rules."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from tegel.register import (
    Context,
    InvalidURIError,
    NamespaceConfig,
    NamespaceNotFoundError,
    NamespaceRestrictedError,
    NamespaceUnavailableError,
    ResolutionLoopError,
)
from tegel.register.namespaces.base import (
    NamespaceRequestContext,
    NamespaceResolution,
    Principal,
    ResolutionStatus,
)
from tegel.register.namespaces.client import NamespaceClient
from tegel.register.namespaces.server import NullAuthenticator, ServerConfig, serve
from tegel.register.site import Site


class MapRouter:
    def __init__(self, mapping: dict[str, NamespaceResolution]) -> None:
        self.mapping = mapping
        self.calls: list[tuple[str, str | None]] = []

    def resolve_namespace(
        self, identifier: str, context: NamespaceRequestContext
    ) -> NamespaceResolution:
        principal_id = context.principal.id if context.principal else None
        self.calls.append((identifier, principal_id))
        assert not hasattr(context, "token")
        assert not hasattr(context, "password")
        if identifier not in self.mapping:
            return NamespaceResolution(status=ResolutionStatus.NOT_FOUND)
        return self.mapping[identifier]


def _context(**kwargs):
    kwargs.setdefault("site", Site.from_mapping({}))
    return Context(**kwargs)


def test_namespace_available():
    target = "file:///data/n.dat"
    router = MapRouter(
        {"abc": NamespaceResolution(status=ResolutionStatus.AVAILABLE, uri=target)}
    )
    with _context(
        namespaces=NamespaceConfig(resolvers={"gov.lbl.mbib": router}),
        principal=Principal(id="alice"),
    ) as ctx:
        resolved, trail = ctx.resolve_chain("gov.lbl.mbib:abc")
        assert resolved == target
        assert trail == ("gov.lbl.mbib:abc",)


def test_namespace_statuses():
    router = MapRouter(
        {
            "gone": NamespaceResolution(status=ResolutionStatus.NOT_FOUND),
            "nope": NamespaceResolution(status=ResolutionStatus.RESTRICTED),
            "tape": NamespaceResolution(
                status=ResolutionStatus.UNAVAILABLE, retry_after=timedelta(hours=1)
            ),
        }
    )
    with _context(namespaces=NamespaceConfig(resolvers={"ns": router})) as ctx:
        with pytest.raises(NamespaceNotFoundError):
            ctx.resolve_chain("ns:gone")
        with pytest.raises(NamespaceRestrictedError):
            ctx.resolve_chain("ns:nope")
        with pytest.raises(NamespaceUnavailableError):
            ctx.resolve_chain("ns:tape")


def test_namespace_cache_keyed_by_principal():
    target = "file:///data/c.dat"
    router = MapRouter(
        {"x": NamespaceResolution(status=ResolutionStatus.AVAILABLE, uri=target)}
    )
    cfg = NamespaceConfig(resolvers={"ns": router})
    with _context(namespaces=cfg, principal=Principal(id="a"), namespace_cache=True) as ctx:
        ctx.resolve_chain("ns:x")
        ctx.resolve_chain("ns:x")
    assert len(router.calls) == 1
    assert router.calls[0][1] == "a"

    with _context(namespaces=cfg, principal=Principal(id="b"), namespace_cache=True) as ctx:
        ctx.resolve_chain("ns:x")
    assert router.calls[-1][1] == "b"


def test_negative_results_are_not_cached():
    router = MapRouter({})
    cfg = NamespaceConfig(resolvers={"ns": router})
    with _context(namespaces=cfg) as ctx:
        for _ in range(2):
            with pytest.raises(NamespaceNotFoundError):
                ctx.resolve_chain("ns:missing")
        router.mapping["nope"] = NamespaceResolution(status=ResolutionStatus.RESTRICTED)
        for _ in range(2):
            with pytest.raises(NamespaceRestrictedError):
                ctx.resolve_chain("ns:nope")
        router.mapping["down"] = NamespaceResolution(status=ResolutionStatus.UNAVAILABLE)
        for _ in range(2):
            with pytest.raises(NamespaceUnavailableError):
                ctx.resolve_chain("ns:down")
    assert router.calls.count(("missing", None)) == 2
    assert router.calls.count(("nope", None)) == 2
    assert router.calls.count(("down", None)) == 2


def test_unavailable_cached_only_for_retry_after(monkeypatch):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock = {"now": start}

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr("tegel.register.namespaces.base.datetime", FakeDateTime)
    router = MapRouter(
        {
            "tape": NamespaceResolution(
                status=ResolutionStatus.UNAVAILABLE, retry_after=timedelta(seconds=30)
            )
        }
    )
    with _context(namespaces=NamespaceConfig(resolvers={"ns": router})) as ctx:
        with pytest.raises(NamespaceUnavailableError):
            ctx.resolve_chain("ns:tape")
        clock["now"] = start + timedelta(seconds=29)
        with pytest.raises(NamespaceUnavailableError):
            ctx.resolve_chain("ns:tape")
        assert len(router.calls) == 1
        clock["now"] = start + timedelta(seconds=30)
        with pytest.raises(NamespaceUnavailableError):
            ctx.resolve_chain("ns:tape")
        assert len(router.calls) == 2


def test_resolution_loop():
    router = MapRouter(
        {"a": NamespaceResolution(status=ResolutionStatus.AVAILABLE, uri="ns:b")},
    )
    router.mapping["b"] = NamespaceResolution(status=ResolutionStatus.AVAILABLE, uri="ns:a")
    with _context(namespaces=NamespaceConfig(resolvers={"ns": router})) as ctx:
        with pytest.raises(ResolutionLoopError):
            ctx.resolve_chain("ns:a")


def test_namespace_fragment_inherited():
    target = "file:///data/frag.dat"
    router = MapRouter(
        {"abc": NamespaceResolution(status=ResolutionStatus.AVAILABLE, uri=target)}
    )
    with _context(namespaces=NamespaceConfig(resolvers={"ns": router})) as ctx:
        resolved, trail = ctx.resolve_chain("ns:abc#frag")
        assert router.calls[-1][0] == "abc"
        assert resolved == f"{target}#frag"
        assert trail == ("ns:abc#frag",)


def test_namespace_fragment_target_wins():
    target = "file:///data/frag.dat"
    router = MapRouter(
        {"abc": NamespaceResolution(status=ResolutionStatus.AVAILABLE, uri=f"{target}#own")}
    )
    with _context(namespaces=NamespaceConfig(resolvers={"ns": router})) as ctx:
        resolved, _trail = ctx.resolve_chain("ns:abc#frag")
        assert resolved == f"{target}#own"


def test_resolution_loop_case_insensitive_scheme():
    router = MapRouter(
        {"abc": NamespaceResolution(status=ResolutionStatus.AVAILABLE, uri="NS:abc")},
    )
    with _context(namespaces=NamespaceConfig(resolvers={"ns": router})) as ctx:
        with pytest.raises(ResolutionLoopError, match="loop"):
            ctx.resolve_chain("ns:abc")
    assert len(router.calls) <= 2


def test_namespace_rejects_double_slash():
    router = MapRouter({})
    with _context(namespaces=NamespaceConfig(resolvers={"ns": router})) as ctx:
        with pytest.raises(InvalidURIError, match="RFC 7595"):
            ctx.resolve_chain("ns://abc")


def test_namespace_single_slash_identifier():
    target = "file:///data/slash.dat"
    router = MapRouter(
        {"/abc": NamespaceResolution(status=ResolutionStatus.AVAILABLE, uri=target)}
    )
    with _context(namespaces=NamespaceConfig(resolvers={"ns": router})) as ctx:
        resolved, _trail = ctx.resolve_chain("ns:/abc")
        assert router.calls[-1][0] == "/abc"
        assert resolved == target


def test_namespace_client_requires_https():
    with pytest.raises(ValueError, match="https"):
        NamespaceClient("http://example.com")
    NamespaceClient("https://example.com")
    NamespaceClient("http://127.0.0.1:8765", allow_insecure_transport=True)
    with pytest.raises(ValueError, match="http\\(s\\)"):
        NamespaceClient("ftp://example.com")


def test_namespace_server_and_client():
    target = "file:///data/s.dat"
    router = MapRouter(
        {
            "id1": NamespaceResolution(status=ResolutionStatus.AVAILABLE, uri=target),
            "missing": NamespaceResolution(status=ResolutionStatus.NOT_FOUND),
        }
    )
    config = ServerConfig(
        namespace="gov.lbl.mbib",
        resolver=router,
        authenticator=NullAuthenticator(),
        host="127.0.0.1",
        port=0,
        include_detail=False,
    )
    server = serve(config, blocking=False)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = NamespaceClient(f"http://{host}:{port}", allow_insecure_transport=True)
        req = NamespaceRequestContext(
            principal=Principal(id="client"), secrets=None, request_id="1"
        )
        ok = client.resolve_namespace("id1", req)
        assert ok.status is ResolutionStatus.AVAILABLE
        assert ok.uri == target
        assert ok.detail is None
        missing = client.resolve_namespace("missing", req)
        assert missing.status is ResolutionStatus.NOT_FOUND
    finally:
        server.shutdown()
