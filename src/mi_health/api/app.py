"""FastAPI app: REST, SPA placeholder, and mounted MCP Streamable HTTP."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mi_health.api.auth import AuthContext, MCPAuthMiddleware, require_scope
from mi_health.config import Settings
from mi_health.credentials import CredentialStore
from mi_health.db import Database
from mi_health.services import NotConfiguredError, QueryService

logger = logging.getLogger(__name__)

ReadAuthDep = Depends(require_scope("read"))
SyncAuthDep = Depends(require_scope("sync"))


class SyncRequest(BaseModel):
    data_types: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None
    force: bool = False


def create_app(settings: Settings, db: Database, store: CredentialStore, mcp=None) -> FastAPI:
    service = QueryService(db, store, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if mcp is not None:
            async with mcp.session_manager.run():
                yield
        else:
            yield

    app = FastAPI(
        title="mi-health-mcp",
        version="0.1.0",
        description="Local-first Xiaomi health data hub for agents",
        lifespan=lifespan,
    )
    app.state.db = db
    app.state.store = store
    app.state.settings = settings
    app.state.service = service
    app.state.mcp = mcp

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"status": "error", "error": {"message": exc.detail, "code": exc.status_code}},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"status": "error", "error": {"message": exc.errors(), "code": 422}},
        )

    @app.exception_handler(NotConfiguredError)
    async def not_configured(request: Request, exc: NotConfiguredError):
        return JSONResponse(
            status_code=503,
            content={"status": "error", "error": {"message": str(exc), "code": 503}},
        )

    @app.exception_handler(ValueError)
    async def bad_value(request: Request, exc: ValueError):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": {"message": str(exc), "code": 400}},
        )

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "service": "mi-health-mcp"}

    @app.get("/api/status")
    def status(_: AuthContext = ReadAuthDep) -> dict[str, Any]:
        return service.status()

    @app.get("/api/summary")
    def summary(
        start_date: str = Query(...),
        end_date: str = Query(...),
        _: AuthContext = ReadAuthDep,
    ) -> dict[str, Any]:
        return service.summary(start_date, end_date)

    @app.get("/api/metric-series")
    def metric_series(
        metric: str = Query(...),
        start_date: str = Query(...),
        end_date: str = Query(...),
        granularity: str = "day",
        _: AuthContext = ReadAuthDep,
    ) -> dict[str, Any]:
        return service.metric_series(metric, start_date, end_date, granularity)

    @app.get("/api/heart-rate")
    def heart_rate(
        start_date: str = Query(...),
        end_date: str = Query(...),
        sample_type: str | None = Query(None),
        limit: int | None = Query(None),
        _: AuthContext = ReadAuthDep,
    ) -> dict[str, Any]:
        return service.heart_rate(start_date, end_date, sample_type, limit)

    @app.get("/api/sleep")
    def sleep(
        start_date: str = Query(...),
        end_date: str = Query(...),
        include_naps: bool = True,
        _: AuthContext = ReadAuthDep,
    ) -> dict[str, Any]:
        return service.sleep(start_date, end_date, include_naps)

    @app.get("/api/workouts")
    def workouts(
        start_date: str = Query(...),
        end_date: str = Query(...),
        _: AuthContext = ReadAuthDep,
    ) -> dict[str, Any]:
        return service.workouts(start_date, end_date)

    @app.get("/api/body-measurements")
    def body_measurements(
        start_date: str = Query(...),
        end_date: str = Query(...),
        _: AuthContext = ReadAuthDep,
    ) -> dict[str, Any]:
        return service.body_measurements(start_date, end_date)

    @app.get("/api/spo2")
    def spo2(
        start_date: str = Query(...),
        end_date: str = Query(...),
        limit: int | None = Query(None),
        _: AuthContext = ReadAuthDep,
    ) -> dict[str, Any]:
        return service.spo2(start_date, end_date, limit)

    @app.get("/api/stress")
    def stress(
        start_date: str = Query(...),
        end_date: str = Query(...),
        limit: int | None = Query(None),
        _: AuthContext = ReadAuthDep,
    ) -> dict[str, Any]:
        return service.stress(start_date, end_date, limit)

    @app.get("/api/abnormal-heart-beat")
    def abnormal_heart_beat(
        start_date: str = Query(...),
        end_date: str = Query(...),
        _: AuthContext = ReadAuthDep,
    ) -> dict[str, Any]:
        return service.abnormal_heart_beat(start_date, end_date)

    @app.get("/api/coverage")
    def coverage(_: AuthContext = ReadAuthDep) -> dict[str, Any]:
        return service.coverage()

    @app.post("/api/sync", status_code=202)
    def sync(
        body: SyncRequest, _: AuthContext = SyncAuthDep
    ) -> dict[str, Any]:
        return service.enqueue_sync(body.data_types, body.start_date, body.end_date, body.force)

    @app.get("/api/sync/{job_id}")
    def sync_status(
        job_id: str, _: AuthContext = ReadAuthDep
    ) -> dict[str, Any]:
        try:
            return service.job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown sync job: {job_id}") from exc

    if mcp is not None:
        mcp_app = mcp.streamable_http_app()
        app.mount("/mcp", MCPAuthMiddleware(mcp_app, db))

    return app
