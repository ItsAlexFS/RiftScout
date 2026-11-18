"""Seeding script: populates Redis Cloud before the demo / tests.

Example
    python -m scripts.seed --reset --players "Name#LAN,Other#1234" --matches 20
It goes through the same services as the API, so the resulting keys are exactly
the ones the application creates.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter

from app import keys
from app.config import get_settings
from app.container import build_services
from app.db import create_redis
from app.errors import AppError, Conflict

async def reset(redis) -> int:
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


async def key_summary(redis) -> Counter:
    counts: Counter = Counter()
    async for k in redis.scan_iter(match=keys.all_keys_pattern(), count=500):
        counts[await redis.type(k)] += 1
    return counts


async def main() -> None:
    ap = argparse.ArgumentParser(description="Seed RiftScout data into Redis Cloud")
    ap.add_argument("--players", default="", help='Riot IDs separated by commas: "Name#TAG,Other#TAG"')
    ap.add_argument("--matches", type=int, default=20, help="matches to sync per player (max 20)")
    ap.add_argument("--reset", action="store_true", help="delete every rift:* key first")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="    %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = get_settings()
    redis = create_redis(settings)
    await redis.ping()
    services = build_services(redis, settings)
    services.riot.limiter.max_wait = 125  # the seed waits for the 2-minute window instead of failing
    try:
        if args.reset:
            print(f"Reset: {await reset(redis)} claves eliminadas")

        targets = []
        for riot_id in filter(None, (x.strip() for x in args.players.split(","))):
            name, _, tag = riot_id.partition("#")
            targets.append((name, tag, "FILL"))
        if not targets and not args.reset:
            ap.error('usa --reset y/o --players "Nombre#TAG,..."')

        for name, tag, role in targets:
            try:
                player = await services.players.create(name, tag, role=role, notes="Cargado por seed")
                print(f"+ {player['riotId']:<18} {player['tier']} {player['rank']} {player['lp']} LP")
            except Conflict:
                player = await services.players.get_by_riot_id(name, tag)
                print(f"= {player['riotId']:<18} ya existía")
            except AppError as exc:
                print(f"! {name}#{tag}: {exc.message}")
                continue
            result = await services.sync.sync_player(player["puuid"], count=min(args.matches, 20), force=True)
            print(f"    sync: {result['newMatches']} nuevas ({result['ingested']} contadas, sin remakes), "
                  f"{result['downloadedFromRiot']} descargadas, {result['reusedFromRedis']} reutilizadas")

        print("\nClaves por tipo:", dict(await key_summary(redis)))
    finally:
        await services.aclose()


if __name__ == "__main__":
    asyncio.run(main())
