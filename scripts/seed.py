"""Seeding script: populates Redis Cloud with real Riot data before the demo / tests.

    python -m scripts.seed --reset                    # players from scripts/players.txt
    python -m scripts.seed --players "Name#LAN,Other#1234" --matches 20
--reset runs the database script (scripts/init_db.py) first. The seed goes through
the same services as the API, so the resulting keys are exactly the ones the
application creates.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from app.api.schemas import Role
from app.config import get_settings
from app.container import build_services
from app.db import create_redis
from app.errors import AppError, Conflict

from .init_db import init_db, key_summary

PLAYERS_FILE = Path(__file__).resolve().parent / "players.txt"
ROLES = Role.__args__


def parse_players(lines: list[str]) -> list[tuple[str, str, str]]:
    """Each line: "Name#TAG" or "Name#TAG,ROLE". Lines starting with # are comments."""
    out = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        riot_id, _, role = (x.strip() for x in line.partition(","))
        name, _, tag = riot_id.rpartition("#")
        role = role.upper() or "FILL"
        if not name or not tag or role not in ROLES:
            raise SystemExit(f"Línea inválida: '{line}' (formato: Nombre#TAG,ROL con ROL en {', '.join(ROLES)})")
        out.append((name, tag, role))
    return out


async def main() -> None:
    ap = argparse.ArgumentParser(description="Seed RiftScout data into Redis Cloud")
    ap.add_argument("--players", default="",
                    help=f'Riot IDs separated by commas: "Name#TAG,Other#TAG" (default: {PLAYERS_FILE.name})')
    ap.add_argument("--matches", type=int, default=20, help="matches to sync per player (max 20)")
    ap.add_argument("--reset", action="store_true", help="run scripts/init_db.py first (deletes every rift:* key)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="    %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = get_settings()
    redis = create_redis(settings)
    await redis.ping()
    services = build_services(redis, settings)
    services.riot.limiter.max_wait = 125  # the seed waits for the 2-minute window instead of failing
    try:
        if args.players:
            targets = parse_players(args.players.split(","))
        else:
            targets = parse_players(PLAYERS_FILE.read_text(encoding="utf-8").splitlines())
        if not targets:
            ap.error(f'no hay jugadores: agrégalos a {PLAYERS_FILE.name} o usa --players "Nombre#TAG,..."')

        if args.reset:
            await init_db(redis)
            print()

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
