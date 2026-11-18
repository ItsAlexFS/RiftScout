"""CRUD of tracked players (the team roster).

Create  -> HSET player hash + SET Riot ID index + SADD roster + ZADD rank board (one MULTI/EXEC)
Read    -> HGETALL (+ pipelined reads of totals / cooldown TTL)
Update  -> HSET of editable fields only
Delete  -> UNLINK every key of the player + SREM/ZREM from shared structures
"""
from __future__ import annotations

import time

from redis.asyncio import Redis

from .. import keys
from ..config import Settings
from ..errors import Conflict, NotFound
from ..riot.client import RiotClient
from .ranking import kda, rank_score, solo_queue_entry

EDITABLE_FIELDS = {"role", "notes"}


def _now_ms() -> str:
    return str(int(time.time() * 1000))


class PlayerService:
    def __init__(self, redis: Redis, riot: RiotClient, settings: Settings):
        self.r = redis
        self.riot = riot
        self.s = settings

    # ------------------------------------------------------------- create
    async def create(self, game_name: str, tag_line: str, role: str = "FILL", notes: str = "") -> dict:
        account = await self.riot.get_account(game_name, tag_line)
        puuid = account["puuid"]
        if await self.r.sismember(keys.roster(), puuid):
            raise Conflict(f"{account['gameName']}#{account['tagLine']} ya está en el roster")

        summoner = await self.riot.get_summoner(puuid)
        solo = solo_queue_entry(await self.riot.get_league_entries(puuid)) or {}
        now = _now_ms()
        data = {
            "puuid": puuid,
            "gameName": account["gameName"],
            "tagLine": account["tagLine"],
            "platform": self.s.riot_platform,
            "profileIconId": str(summoner.get("profileIconId", 0)),
            "summonerLevel": str(summoner.get("summonerLevel", 0)),
            "tier": solo.get("tier", "UNRANKED"),
            "rank": solo.get("rank", ""),
            "lp": str(solo.get("leaguePoints", 0)),
            "rankedWins": str(solo.get("wins", 0)),
            "rankedLosses": str(solo.get("losses", 0)),
            "role": role,
            "notes": notes,
            "createdAt": now,
            "updatedAt": now,
            "lastSyncAt": "0",
        }
        score = rank_score(solo.get("tier"), solo.get("rank"), solo.get("leaguePoints"))

        pipe = self.r.pipeline(transaction=True)  # MULTI ... EXEC: all or nothing
        pipe.hset(keys.player(puuid), mapping=data)
        pipe.set(keys.riot_id_index(account["gameName"], account["tagLine"]), puuid)
        pipe.sadd(keys.roster(), puuid)
        if score is not None:
            pipe.zadd(keys.leaderboard("rank"), {puuid: score})
        pipe.lpush(keys.recent_searches(), f"{account['gameName']}#{account['tagLine']}")
        pipe.ltrim(keys.recent_searches(), 0, 9)
        await pipe.execute()
        return await self.get(puuid)

    # --------------------------------------------------------------- read
    async def get(self, puuid: str) -> dict:
        pipe = self.r.pipeline(transaction=False)
        pipe.hgetall(keys.player(puuid))
        pipe.hgetall(keys.player_totals(puuid))
        pipe.ttl(keys.sync_cooldown(puuid))
        player, totals, cooldown = await pipe.execute()
        if not player:
            raise NotFound("Jugador no encontrado en el roster")
        return self._decorate(player, totals, cooldown)

    async def get_by_riot_id(self, game_name: str, tag_line: str) -> dict:
        puuid = await self.r.get(keys.riot_id_index(game_name, tag_line))
        if not puuid:
            raise NotFound("Ese Riot ID no está en el roster")
        return await self.get(puuid)

    async def list_all(self) -> list[dict]:
        puuids = list(await self.r.smembers(keys.roster()))
        if not puuids:
            return []
        pipe = self.r.pipeline(transaction=False)
        for p in puuids:
            pipe.hgetall(keys.player(p))
            pipe.hgetall(keys.player_totals(p))
            pipe.ttl(keys.sync_cooldown(p))
        res = await pipe.execute()
        players = [self._decorate(res[i], res[i + 1], res[i + 2]) for i in range(0, len(res), 3) if res[i]]
        return sorted(players, key=lambda p: p["rankScore"] or -1, reverse=True)

    # ------------------------------------------------------------- update
    async def update(self, puuid: str, changes: dict) -> dict:
        changes = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS and v is not None}
        if not await self.r.exists(keys.player(puuid)):
            raise NotFound("Jugador no encontrado en el roster")
        if changes:
            await self.r.hset(keys.player(puuid), mapping={**changes, "updatedAt": _now_ms()})
        return await self.get(puuid)

    # ------------------------------------------------------------- delete
    async def delete(self, puuid: str) -> dict:
        player = await self.r.hgetall(keys.player(puuid))
        if not player:
            raise NotFound("Jugador no encontrado en el roster")
        champ_games = await self.r.zrange(keys.player_champ_games(puuid), 0, -1, withscores=True)

        pipe = self.r.pipeline(transaction=True)
        pipe.unlink(
            keys.player(puuid), keys.player_matches(puuid), keys.player_seen(puuid),
            keys.player_totals(puuid), keys.player_champ_games(puuid), keys.player_champ_wins(puuid),
            keys.riot_id_index(player["gameName"], player["tagLine"]), keys.sync_cooldown(puuid),
        )
        pipe.srem(keys.roster(), puuid)
        for board in keys.LEADERBOARDS:
            pipe.zrem(keys.leaderboard(board), puuid)
        for champ, games in champ_games:  # remove this player's games from the roster-wide ranking
            pipe.zincrby(keys.roster_champs(), -games, champ)
        pipe.zremrangebyscore(keys.roster_champs(), "-inf", 0)
        await pipe.execute()
        # rift:match:* hashes are kept on purpose: a match can be shared by several roster players.
        return {"deleted": puuid, "riotId": f"{player['gameName']}#{player['tagLine']}"}

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _decorate(player: dict, totals: dict, cooldown_ttl: int) -> dict:
        games = int(totals.get("games", 0))
        wins = int(totals.get("wins", 0))
        k, d, a = (int(totals.get(x, 0)) for x in ("kills", "deaths", "assists"))
        return {
            **player,
            "riotId": f"{player['gameName']}#{player['tagLine']}",
            "rankScore": rank_score(player.get("tier"), player.get("rank"), player.get("lp")),
            "stats": {
                "games": games,
                "wins": wins,
                "winrate": round(100 * wins / games, 1) if games else None,
                "kda": kda(k, d, a) if games else None,
                "avgKills": round(k / games, 1) if games else None,
                "avgDeaths": round(d / games, 1) if games else None,
                "avgAssists": round(a / games, 1) if games else None,
                "csPerMin": round(int(totals.get("cs", 0)) / (int(totals.get("seconds", 1)) / 60), 1) if games else None,
            },
            "syncCooldown": max(0, cooldown_ttl),
        }
