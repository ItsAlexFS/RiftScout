"""Database script: prepares the Redis Cloud database before seeding.

    python -m scripts.init_db

Redis has no schema (no CREATE TABLE): every structure is created by the first
write to its key. So "creating the database" here means:
  1. Check the connection to Redis Cloud and the server settings the design
     relies on (eviction policy).
  2. Delete every key of this app (SCAN + UNLINK on rift:*, never FLUSHDB), so
     the database starts empty and other apps sharing it are not touched.
  3. Create the structures that must exist before any player is added.
  4. Print the key schema defined in app/keys.py.
"""
from __future__ import annotations

import asyncio
import time
from collections import Counter

from redis.asyncio import Redis

from app import keys
from app.config import get_settings
from app.db import create_redis

# With volatile-* policies Redis can only evict keys that have a TTL (cache),
# never the roster or the matches, which have no TTL.
SAFE_EVICTION = ("volatile-lru", "volatile-lfu", "volatile-ttl", "volatile-random", "noeviction")


async def check_server(redis: Redis) -> None:
    t0 = time.perf_counter()
    await redis.ping()
    ping_ms = (time.perf_counter() - t0) * 1000
    server = await redis.info("server")
    memory = await redis.info("memory")
    conn = redis.connection_pool.connection_kwargs
    policy = memory.get("maxmemory_policy")
    print(f"Conectado a {conn.get('host')}:{conn.get('port')} · Redis {server.get('redis_version')} · ping {ping_ms:.1f} ms")
    print(f"Memoria usada: {memory.get('used_memory_human')} · eviction policy: {policy}")
    if policy not in SAFE_EVICTION:
        print(f"  AVISO: con '{policy}' Redis podría expulsar datos del roster. "
              "Cámbiala a volatile-lru en Redis Cloud > Configuration > Durability.")


async def reset(redis: Redis) -> int:
    """Delete only this app's keys (SCAN + UNLINK), never FLUSHDB."""
    removed, batch = 0, []
    async for k in redis.scan_iter(match=keys.all_keys_pattern(), count=500):
        batch.append(k)
        if len(batch) == 500:
            removed += await redis.unlink(*batch)
            batch.clear()
    if batch:
        removed += await redis.unlink(*batch)
    return removed


async def create_base_structures(redis: Redis) -> None:
    """Counters read by the "Redis en vivo" panel start at 0 instead of missing."""
    await redis.hset(keys.cache_stats(), mapping={"hits": 0, "misses": 0, "riot_calls": 0})


async def key_summary(redis: Redis) -> Counter:
    counts: Counter = Counter()
    async for k in redis.scan_iter(match=keys.all_keys_pattern(), count=500):
        counts[await redis.type(k)] += 1
    return counts


async def init_db(redis: Redis) -> None:
    await check_server(redis)
    print(f"Reset: {await reset(redis)} claves {keys.all_keys_pattern()} eliminadas")
    await create_base_structures(redis)
    print(f"Creado {keys.cache_stats()} (hits, misses, riot_calls = 0)")


async def main() -> None:
    redis = create_redis(get_settings())
    try:
        await init_db(redis)
        table = keys.__doc__.split("\n\n", 2)[-1].strip()  # the key table documented in app/keys.py
        print("\nEsquema de claves (app/keys.py):\n" + table)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
