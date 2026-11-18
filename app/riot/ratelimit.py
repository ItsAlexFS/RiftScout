"""Distributed fixed-window rate limiter for the Riot API, stored in Redis.

Each window is a counter key named after its time bucket, e.g.
    rift:rl:riot:1s:1763418000      (expires after 2 s)
    rift:rl:riot:120s:14695150      (expires after 121 s)
INCR + EXPIRE run inside a MULTI/EXEC transaction, so every worker or process
that shares the database shares the same budget.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time

from redis.asyncio import Redis

from .. import keys
from ..config import Settings
from ..errors import RiotRateLimited

log = logging.getLogger("riftscout.ratelimit")
MAX_WAIT_SECONDS = 3.0  # API requests fail fast; the seed script raises this to wait for the window


class RiotRateLimiter:
    def __init__(self, redis: Redis, settings: Settings):
        self.r = redis
        self.windows = ((1, settings.riot_limit_per_second), (120, settings.riot_limit_per_2min))
        self.max_wait = MAX_WAIT_SECONDS

    async def acquire(self) -> None:
        while True:
            now = time.time()
            pipe = self.r.pipeline(transaction=True)
            for window, _limit in self.windows:
                k = keys.rate_limit(f"{window}s", int(now // window))
                pipe.incr(k)
                pipe.expire(k, window + 1)
            results = await pipe.execute()
            counts = results[0::2]

            exceeded = [w for (w, limit), c in zip(self.windows, counts) if c > limit]
            if not exceeded:
                return
            wait = max(w - (now % w) for w in exceeded)
            if wait > self.max_wait:
                raise RiotRateLimited(
                    "Límite de la API de Riot alcanzado, intenta más tarde",
                    retry_after=math.ceil(wait),
                )
            log.info("Límite de Riot alcanzado: esperando %.1f s", wait)
            await asyncio.sleep(wait)

    async def snapshot(self) -> list[dict]:
        """Current usage of each window (for the system panel)."""
        now = time.time()
        out = []
        for window, limit in self.windows:
            k = keys.rate_limit(f"{window}s", int(now // window))
            used = int(await self.r.get(k) or 0)
            out.append({"window_s": window, "limit": limit, "used": used, "key": k,
                        "resets_in_s": round(window - (now % window), 1)})
        return out
