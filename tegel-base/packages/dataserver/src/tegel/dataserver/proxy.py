"""Stream a ``proxy`` part from its upstream Tiled server.

The upstream URL is built only from the stored ``upstream`` and
``remote_path``. Only GET is proxied, only listed query parameters are
forwarded, and the caller's ``Authorization`` header never leaves Tegel.
"""

from __future__ import annotations

import threading
from typing import Any

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from tegel.core.contracts.resources import DataPart, StructureFamily
from tegel.core.upstreams import Upstream, UpstreamAuth
from tegel.dataserver.parts import tiled_route

FORWARDED_PARAMS = ("slice", "block", "expected_shape", "format", "column", "field", "partition")
FORWARDED_HEADERS = ("accept", "range")
RETURNED_HEADERS = (
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "content-encoding",
    "etag",
)


class UpstreamClients:
    """One shared ``httpx.Client`` per upstream, created on first use.

    ``transport`` replaces the network for tests.
    """

    def __init__(
        self,
        upstreams: dict[str, Upstream],
        *,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.upstreams = upstreams
        self.timeout = timeout
        self.transport = transport
        self._clients: dict[str, httpx.Client] = {}
        self._lock = threading.Lock()

    def client(self, upstream: Upstream) -> httpx.Client:
        with self._lock:
            found = self._clients.get(upstream.origin)
            if found is None:
                found = httpx.Client(
                    auth=UpstreamAuth(upstream),
                    timeout=self.timeout,
                    transport=self.transport,
                    follow_redirects=False,
                )
                self._clients[upstream.origin] = found
            return found

    def close(self) -> None:
        with self._lock:
            for client in self._clients.values():
                client.close()
            self._clients.clear()


def upstream_route(part: DataPart, params: dict[str, Any]) -> str:
    """Pick the upstream Tiled route from the part's family and the request."""
    if part.family is StructureFamily.array and "block" in params:
        return "array/block"
    if part.family is StructureFamily.table and "partition" in params:
        return "table/partition"
    return tiled_route(part.family)


def proxy_part(part: DataPart, request: Request, clients: UpstreamClients) -> StreamingResponse:
    upstream = clients.upstreams.get(part.upstream or "")
    if upstream is None or not part.remote_path:
        raise HTTPException(status_code=502, detail=f"Upstream {part.upstream} is not configured")
    params = [
        (name, value)
        for name, value in request.query_params.multi_items()
        if name in FORWARDED_PARAMS
    ]
    route = upstream_route(part, dict(params))
    headers = {
        name: request.headers[name] for name in FORWARDED_HEADERS if name in request.headers
    }
    client = clients.client(upstream)
    outgoing = client.build_request(
        "GET", f"{upstream.base}/{route}/{part.remote_path}", params=params, headers=headers
    )
    try:
        response = client.send(outgoing, stream=True)
    except httpx.TimeoutException as exc:
        raise _failed(upstream, 504, "timed out") from exc
    except httpx.HTTPError as exc:
        raise _failed(upstream, 502, str(exc) or type(exc).__name__) from exc
    status = response.status_code
    if status in (401, 403):
        response.close()
        raise HTTPException(status_code=502, detail="Upstream refused Tegel's credentials")
    if status == 404:
        response.close()
        raise HTTPException(status_code=404, detail="Upstream node not found")
    if not (200 <= status < 300 or status == 416):
        response.close()
        raise _failed(upstream, 504 if status == 504 else 502, f"HTTP {status}")
    returned = {
        name: response.headers[name] for name in RETURNED_HEADERS if name in response.headers
    }
    return StreamingResponse(
        response.iter_raw(),
        status_code=status,
        headers=returned,
        background=BackgroundTask(response.close),
    )


def _failed(upstream: Upstream, status: int, reason: str) -> HTTPException:
    return HTTPException(status_code=status, detail=f"Upstream {upstream.origin} failed: {reason}")
