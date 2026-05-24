"""FastAPI entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import (
    api_keys,
    bridge,
    bridge_runner,
    frameworks,
    health,
    integrations,
    jira,
    plans,
    runs_live,
    workspaces,
)

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("api.startup", env=settings.env, app=settings.app_name)
    yield
    log.info("api.shutdown")


app = FastAPI(
    title=f"{settings.app_name} API",
    version="0.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(workspaces.router, prefix="/api/v1", tags=["workspaces"])
app.include_router(api_keys.router, prefix="/api/v1")
app.include_router(integrations.router, prefix="/api/v1")
app.include_router(bridge.router, prefix="/api/v1")
app.include_router(bridge_runner.router, prefix="/api/v1")
app.include_router(jira.router, prefix="/api/v1")
app.include_router(frameworks.router, prefix="/api/v1")
app.include_router(plans.router, prefix="/api/v1")
app.include_router(runs_live.router, prefix="/api/v1")
