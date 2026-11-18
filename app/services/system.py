"""Observability endpoints used during the live demo: connection health,
volatile keys with their remaining TTL, cache hit ratio and rate limit usage."""
from __future__ import annotations

import time

from redis.asyncio import Redis

from .. import keys
from ..riot.ratelimit import RiotRateLimiter


class SystemService:
    def __init__(self, redis: Redis, limiter: RiotRateLimiter):
        self.r = redis
        self.limiter = limiter

    async def health(self) -> dict:
        t0 = time.perf_counter()
        await self.r.ping()
        latency = (time.perf_counter() - t0) * 1000
        server = await self.r.info("server")
        memory = await self.r.info("memory")
        conn = self.r.connection_pool.connection_kwargs
        return {
            "status": "ok",
            "redisHost": f"{conn.get('host')}:{conn.get('port')}",
            "redisVersion": server.get("redis_version"),
            "pingMs": round(latency, 2),
            "dbSize": await self.r.dbsize(),
            "usedMemory": memory.get("used_memory_human"),
            "maxMemoryPolicy": memory.get("maxmemory_policy"),
        }

    async def volatile_keys(self, limit: int = 200) -> list[dict]:
        """SCAN (never KEYS) the volatile namespaces and report TYPE + TTL."""
        found: list[str] = []
        for pattern in keys.VOLATILE_PATTERNS:
            async for k in self.r.scan_iter(match=pattern, count=200):
                found.append(k)
                if len(found) >= limit:
                    break
            if len(found) >= limit:
                break
        pipe = self.r.pipeline(transaction=False)
        for k in found:
            pipe.type(k)
            pipe.ttl(k)
        res = await pipe.execute()
        rows = [{"key": k, "type": res[i * 2], "ttl": res[i * 2 + 1]} for i, k in enumerate(found)]
        return sorted(rows, key=lambda x: x["key"])

    async def cache_stats(self) -> dict:
        s = await self.r.hgetall(keys.cache_stats())
        hits, misses = int(s.get("hits", 0)), int(s.get("misses", 0))
        return {
            "hits": hits,
            "misses": misses,
            "hitRatio": round(100 * hits / (hits + misses), 1) if hits + misses else None,
            "riotCalls": int(s.get("riot_calls", 0)),
            "rateLimit": await self.limiter.snapshot(),
            "recentSearches": await self.r.lrange(keys.recent_searches(), 0, -1),
        }

    async def purge_cache(self) -> int:
        """UNLINK every rift:cache:* key (forces the next calls to miss)."""
        removed = 0
        batch: list[str] = []
        async for k in self.r.scan_iter(match=keys.cache_pattern(), count=500):
            batch.append(k)
            if len(batch) >= 500:
                removed += await self.r.unlink(*batch)
                batch.clear()
        if batch:
            removed += await self.r.unlink(*batch)
        return removed
