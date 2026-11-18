"""Redis Cloud connection (official redis-py client, asyncio flavour)."""
from __future__ import annotations

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from .config import Settings


def create_redis(settings: Settings) -> Redis:
    """Build a pooled client. decode_responses=True returns str instead of bytes."""
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=settings.redis_max_connections,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,  # PING idle connections so cloud LB drops are detected
        retry=Retry(ExponentialBackoff(cap=2, base=0.1), retries=3),
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
        client_name="riftscout",
    )
