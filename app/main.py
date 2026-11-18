"""FastAPI entry point:  uvicorn app.main:app --reload"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from redis.exceptions import RedisError

from .api.routes import router
from .config import get_settings
from .container import build_services
from .db import create_redis
from .errors import AppError

log = logging.getLogger("riftscout")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    redis = create_redis(settings)
    await redis.ping()  # fail fast if the cloud database is unreachable
    log.info("Conectado a Redis Cloud (%s)", redis.connection_pool.connection_kwargs.get("host"))
    app.state.services = build_services(redis, settings)
    yield
    await app.state.services.aclose()


app = FastAPI(title="RiftScout", version="1.0.0",
              description="Scouting de partidas de League of Legends sobre Redis Cloud",
              lifespan=lifespan)
app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html")


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError):
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    body = {"error": exc.message}
    if exc.retry_after:
        body["retryAfter"] = exc.retry_after
    return JSONResponse(body, status_code=exc.status_code, headers=headers)


@app.exception_handler(RedisError)
async def redis_error_handler(_: Request, exc: RedisError):
    log.exception("Redis error")
    return JSONResponse({"error": f"Error de Redis: {exc.__class__.__name__}"}, status_code=503)
