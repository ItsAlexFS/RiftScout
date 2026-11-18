"""Wires Redis, the Riot client and the services together (used by the API,
the seeding script and the tests)."""
from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from .config import Settings
from .riot.client import RiotClient
from .services.players import PlayerService
from .services.stats import StatsService
from .services.sync import SyncService
from .services.system import SystemService


@dataclass
class Services:
    redis: Redis
    riot: RiotClient
    players: PlayerService
    sync: SyncService
    stats: StatsService
    system: SystemService

    async def aclose(self) -> None:
        await self.riot.aclose()
        await self.redis.aclose()


def build_services(redis: Redis, settings: Settings) -> Services:
    riot = RiotClient(redis, settings)
    return Services(
        redis=redis,
        riot=riot,
        players=PlayerService(redis, riot, settings),
        sync=SyncService(redis, riot, settings),
        stats=StatsService(redis),
        system=SystemService(redis, riot.limiter),
    )
