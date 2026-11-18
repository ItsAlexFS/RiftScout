"""Tests run against an in-memory fake Redis (fakeredis) and a fake Riot
client with fixed data, so they need neither network nor credentials.
The fake only exists here; the application always calls the real Riot API."""
import os

os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import fakeredis  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.container import build_services  # noqa: E402

from .fake_riot import FakeRiotClient  # noqa: E402


@pytest_asyncio.fixture
async def services(monkeypatch):
    monkeypatch.setattr("app.container.RiotClient", FakeRiotClient)
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    svc = build_services(redis, get_settings())
    yield svc
    await redis.flushall()
    await svc.aclose()
