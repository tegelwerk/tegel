"""Action routes (specs/actionserver.md, HTTP API), and the scope gate on the read side."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from tegel.actionserver.auth import ADMIN, READ, has_scope
from tegel.actionserver.service import ActionService, RequestError
from tegel.core.contracts.actions import ActionCard, Batch, BatchRequest, Plan, Run, RunRequest
from tegel.core.contracts.capabilities import Capabilities, Principal
from tegel.core.version import SPEC_VERSION
from tegel.dataserver.auth.keycloak import AuthenticationError

ACTION_PREFIXES = ("/actions", "/runs", "/batches", "/plans")
OPEN_PATHS = ("/docs", "/openapi.json", "/redoc", "/healthz")


def _principal(request: Request) -> Principal:
    try:
        return request.app.state.tegel_auth.authenticate(request.headers.get("authorization"))
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="Authentication required") from exc


def _require(principal: Principal, scope: str) -> None:
    if not has_scope(principal, scope):
        raise HTTPException(status_code=403, detail=f"this needs scope {scope}")


def _service(request: Request) -> ActionService:
    return request.app.state.tegel_actions


def _refused(exc: RequestError) -> JSONResponse:
    body: dict[str, Any] = {"detail": exc.detail}
    if exc.plan is not None:
        body["plan"] = exc.plan.model_dump(mode="json")
    return JSONResponse(status_code=exc.status, content=body)


def mount_action_routes(app: FastAPI) -> None:
    @app.middleware("http")
    async def read_scope(request: Request, call_next):
        """Everything that is not an action route reads the results catalog: ``actions:read``."""
        path = request.url.path
        root = request.scope.get("root_path", "")
        local = path[len(root):] if root and path.startswith(root) else path
        if local.startswith(ACTION_PREFIXES) or local.startswith(OPEN_PATHS) \
                or local == "/capabilities":
            return await call_next(request)
        try:
            principal = request.app.state.tegel_auth.authenticate(
                request.headers.get("authorization")
            )
        except AuthenticationError:
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        if not has_scope(principal, READ):
            return JSONResponse(status_code=403, content={"detail": f"this needs scope {READ}"})
        return await call_next(request)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/capabilities", response_model=Capabilities, response_model_by_alias=True)
    async def capabilities(request: Request,
                           principal: Principal = Depends(_principal)) -> Capabilities:
        state = request.app.state
        profile = state.tegel_profile
        return Capabilities(
            spec_version=SPEC_VERSION,
            server_version=state.tegel_server_version,
            plugins=state.tegel_registry.installed(),
            search_document="ro-crate",
            auth=state.tegel_auth.name,
            schema_id=profile.schema_id,
            schema_version=profile.schema_version,
            class_name=profile.class_name,
            services=["read", "actions"],
        )

    @app.post("/refresh-catalog")
    async def refresh_catalog(request: Request,
                              principal: Principal = Depends(_principal)) -> dict[str, bool]:
        """The data server's refresh, limited to administrators on the action server."""
        from tegel.dataserver.routes import reopen_catalog

        _require(principal, ADMIN)
        await reopen_catalog(request)
        return {"refreshed": True}

    @app.get("/actions", response_model=list[ActionCard])
    async def list_actions(request: Request,
                           principal: Principal = Depends(_principal)) -> list[ActionCard]:
        _require(principal, READ)
        return _service(request).cards(principal)

    @app.get("/actions/{action_id}", response_model=ActionCard)
    async def get_action(action_id: str, request: Request,
                         principal: Principal = Depends(_principal)) -> Any:
        _require(principal, READ)
        try:
            return _service(request).card(action_id, principal)
        except RequestError as exc:
            return _refused(exc)

    @app.post("/plans", response_model=Plan)
    async def plan(body: RunRequest, request: Request,
                   principal: Principal = Depends(_principal)) -> Any:
        try:
            return await asyncio.to_thread(_service(request).plan, body, principal,
                                           request.headers.get("authorization"))
        except RequestError as exc:
            return _refused(exc)

    @app.post("/runs", response_model=Run, status_code=202)
    async def submit(body: RunRequest, request: Request,
                     principal: Principal = Depends(_principal)) -> Any:
        try:
            return await asyncio.to_thread(_service(request).submit, body, principal,
                                           request.headers.get("authorization"))
        except RequestError as exc:
            return _refused(exc)

    @app.post("/batches", response_model=Batch, status_code=202)
    async def submit_batch(body: BatchRequest, request: Request,
                           principal: Principal = Depends(_principal)) -> Any:
        try:
            return await asyncio.to_thread(_service(request).submit_batch, body, principal,
                                           request.headers.get("authorization"))
        except RequestError as exc:
            return _refused(exc)

    @app.get("/runs", response_model=list[Run])
    async def list_runs(
        request: Request,
        principal: Principal = Depends(_principal),
        action: str | None = None,
        state: str | None = None,
        batch: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[Run]:
        _require(principal, READ)
        return _service(request).list(principal, action=action, state=state, batch=batch,
                                      limit=limit)

    @app.get("/runs/{run_id}", response_model=Run)
    async def get_run(run_id: str, request: Request,
                      principal: Principal = Depends(_principal)) -> Any:
        _require(principal, READ)
        try:
            return _service(request).visible(run_id, principal)
        except RequestError as exc:
            return _refused(exc)

    @app.post("/runs/{run_id}/wait", response_model=Run)
    async def wait(run_id: str, request: Request, timeout: float = Query(default=60, ge=0),
                   principal: Principal = Depends(_principal)) -> Any:
        _require(principal, READ)
        try:
            return await asyncio.to_thread(_service(request).wait, run_id, principal, timeout)
        except RequestError as exc:
            return _refused(exc)

    @app.delete("/runs/{run_id}", response_model=Run)
    async def cancel(run_id: str, request: Request,
                     principal: Principal = Depends(_principal)) -> Any:
        try:
            return _service(request).cancel(run_id, principal)
        except RequestError as exc:
            return _refused(exc)
