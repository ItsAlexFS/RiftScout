"""Pull a player's latest matches from Riot and fold them into Redis.

Concurrency & idempotency patterns used here:
  * Cooldown  -> SET rift:cooldown:sync:{puuid} 1 EX 60   (TTL tells the UI how long to wait)
  * Lock      -> SET rift:lock:sync:{puuid} <token> NX EX 30, released with a Lua
                 compare-and-delete so a slow worker never frees someone else's lock
  * Dedup     -> SMISMEMBER rift:player:{puuid}:seen before counting a match
  * Shared    -> a match already stored as rift:match:{id} is never downloaded again,
                 even if it was first ingested for another roster player
"""
from __future__ import annotations

import json
import time
import uuid

from redis.asyncio import Redis

from .. import keys
from ..config import Settings
from ..errors import Conflict, NotFound, TooManyRequests
from ..riot.client import RiotClient
from .ranking import compact_match, kda, participant, position_name, rank_score, solo_queue_entry

RELEASE_LOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class SyncService:
    def __init__(self, redis: Redis, riot: RiotClient, settings: Settings):
        self.r = redis
        self.riot = riot
        self.s = settings
        self._release_lock = redis.register_script(RELEASE_LOCK_LUA)

    async def sync_player(self, puuid: str, count: int = 10, force: bool = False) -> dict:
        if not await self.r.exists(keys.player(puuid)):
            raise NotFound("Jugador no encontrado en el roster")

        if not force:
            remaining = await self.r.ttl(keys.sync_cooldown(puuid))
            if remaining > 0:
                raise TooManyRequests(f"Espera {remaining} s para volver a sincronizar", retry_after=remaining)

        token = uuid.uuid4().hex
        if not await self.r.set(keys.sync_lock(puuid), token, nx=True, ex=self.s.sync_lock_ttl):
            raise Conflict("Ya hay una sincronización en curso para este jugador")

        started = time.perf_counter()
        calls_before = int(await self.r.hget(keys.cache_stats(), "riot_calls") or 0)
        try:
            await self._refresh_rank(puuid)
            ids = await self.riot.get_match_ids(puuid, count=count, queue=self.s.match_queue)
            seen_flags = await self.r.smismember(keys.player_seen(puuid), ids) if ids else []
            new_ids = [mid for mid, seen in zip(ids, seen_flags) if not seen]

            downloaded = 0
            ingested = 0
            for match_id in reversed(new_ids):  # oldest first, so LPUSH leaves the newest on top
                match = await self.r.hgetall(keys.match(match_id))
                if not match:
                    match = compact_match(await self.riot.get_match(match_id))
                    await self.r.hset(keys.match(match_id), mapping=match)
                    downloaded += 1
                ingested += await self._ingest(puuid, match)

            await self._refresh_ratio_boards(puuid)
            await self.r.hset(keys.player(puuid), "lastSyncAt", str(int(time.time() * 1000)))
            if not force:
                await self.r.set(keys.sync_cooldown(puuid), 1, ex=self.s.sync_cooldown)
        finally:
            await self._release_lock(keys=[keys.sync_lock(puuid)], args=[token])

        calls_after = int(await self.r.hget(keys.cache_stats(), "riot_calls") or 0)
        return {
            "puuid": puuid,
            "matchIdsChecked": len(ids),
            "newMatches": len(new_ids),
            "ingested": ingested,
            "downloadedFromRiot": downloaded,
            "reusedFromRedis": len(new_ids) - downloaded,
            "riotCalls": calls_after - calls_before,
            "elapsedMs": round((time.perf_counter() - started) * 1000),
        }

    # ------------------------------------------------------------ internals
    async def _refresh_rank(self, puuid: str) -> None:
        summoner = await self.riot.get_summoner(puuid)
        solo = solo_queue_entry(await self.riot.get_league_entries(puuid)) or {}
        await self.r.hset(keys.player(puuid), mapping={
            "summonerLevel": str(summoner.get("summonerLevel", 0)),
            "profileIconId": str(summoner.get("profileIconId", 0)),
            "tier": solo.get("tier", "UNRANKED"),
            "rank": solo.get("rank", ""),
            "lp": str(solo.get("leaguePoints", 0)),
            "rankedWins": str(solo.get("wins", 0)),
            "rankedLosses": str(solo.get("losses", 0)),
        })
        score = rank_score(solo.get("tier"), solo.get("rank"), solo.get("leaguePoints"))
        if score is None:
            await self.r.zrem(keys.leaderboard("rank"), puuid)
        else:
            await self.r.zadd(keys.leaderboard("rank"), {puuid: score})

    async def _ingest(self, puuid: str, match: dict) -> int:
        """Fold one match into the player's aggregates. Returns 1 if counted."""
        me = participant(match, puuid)
        pipe = self.r.pipeline(transaction=True)
        pipe.sadd(keys.player_seen(puuid), match["matchId"])
        if me is None or me["remake"]:  # remakes are marked as seen but not counted
            await pipe.execute()
            return 0

        pipe.lpush(keys.player_matches(puuid), match["matchId"])
        pipe.ltrim(keys.player_matches(puuid), 0, self.s.recent_matches_cap - 1)
        totals = keys.player_totals(puuid)
        pipe.hincrby(totals, "games", 1)
        pipe.hincrby(totals, "wins", int(me["win"]))
        pipe.hincrby(totals, "kills", me["k"])
        pipe.hincrby(totals, "deaths", me["d"])
        pipe.hincrby(totals, "assists", me["a"])
        pipe.hincrby(totals, "cs", me["cs"])
        pipe.hincrby(totals, "damage", me["dmg"])
        pipe.hincrby(totals, "seconds", int(match["gameDuration"]))
        pipe.zincrby(keys.player_champ_games(puuid), 1, me["champion"])
        if me["win"]:
            pipe.zincrby(keys.player_champ_wins(puuid), 1, me["champion"])
        pipe.zincrby(keys.roster_champs(), 1, me["champion"])
        await pipe.execute()
        return 1

    async def _refresh_ratio_boards(self, puuid: str) -> None:
        t = await self.r.hgetall(keys.player_totals(puuid))
        games = int(t.get("games", 0))
        if not games:
            return
        pipe = self.r.pipeline(transaction=True)
        pipe.zadd(keys.leaderboard("winrate"), {puuid: round(100 * int(t["wins"]) / games, 2)})
        pipe.zadd(keys.leaderboard("kda"), {puuid: kda(int(t["kills"]), int(t["deaths"]), int(t["assists"]))})
        await pipe.execute()


def decode_participants(match: dict) -> list[dict]:
    parts = json.loads(match.get("participants", "[]"))
    for p in parts:  # matches stored before the rename may still say UTILITY/MIDDLE/BOTTOM
        p["position"] = position_name(p.get("position", ""))
    return parts
