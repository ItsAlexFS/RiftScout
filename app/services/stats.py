"""Read-side queries: match history, champion pools and leaderboards."""
from __future__ import annotations

from redis.asyncio import Redis

from .. import keys
from ..errors import NotFound
from .ranking import kda
from .sync import decode_participants


class StatsService:
    def __init__(self, redis: Redis):
        self.r = redis

    async def player_matches(self, puuid: str, limit: int = 10) -> list[dict]:
        """LRANGE the player's list, then HGETALL each match in one round trip."""
        if not await self.r.exists(keys.player(puuid)):
            raise NotFound("Jugador no encontrado en el roster")
        match_ids = await self.r.lrange(keys.player_matches(puuid), 0, limit - 1)
        roster = await self.r.smembers(keys.roster())
        pipe = self.r.pipeline(transaction=False)
        for mid in match_ids:
            pipe.hgetall(keys.match(mid))
        out = []
        for match in await pipe.execute():
            if not match:
                continue
            parts = decode_participants(match)
            me = next((p for p in parts if p["puuid"] == puuid), None)
            if not me:
                continue
            out.append({
                "matchId": match["matchId"],
                "gameCreation": int(match["gameCreation"]),
                "durationMin": round(int(match["gameDuration"]) / 60, 1),
                "champion": me["champion"],
                "position": me["position"],
                "k": me["k"], "d": me["d"], "a": me["a"],
                "kda": kda(me["k"], me["d"], me["a"]),
                "cs": me["cs"],
                "win": me["win"],
                # teammates that are also in our roster (duo detection)
                "rosterAllies": [p["riotId"] for p in parts
                                 if p["puuid"] in roster and p["puuid"] != puuid and p["team"] == me["team"]],
            })
        return out

    async def match_detail(self, match_id: str) -> dict:
        match = await self.r.hgetall(keys.match(match_id))
        if not match:
            raise NotFound("Partida no encontrada en Redis")
        roster = await self.r.smembers(keys.roster())
        parts = decode_participants(match)
        for p in parts:
            p["inRoster"] = p["puuid"] in roster
            p["kda"] = kda(p["k"], p["d"], p["a"])
        return {
            "matchId": match["matchId"],
            "queueId": int(match["queueId"]),
            "gameCreation": int(match["gameCreation"]),
            "durationMin": round(int(match["gameDuration"]) / 60, 1),
            "gameVersion": match["gameVersion"],
            "winningTeam": int(match["winningTeam"]),
            "teams": {
                "100": [p for p in parts if p["team"] == 100],
                "200": [p for p in parts if p["team"] == 200],
            },
        }

    async def champion_pool(self, puuid: str, top: int = 5) -> list[dict]:
        """ZREVRANGE games + ZMSCORE wins = most played champions with winrate."""
        if not await self.r.exists(keys.player(puuid)):
            raise NotFound("Jugador no encontrado en el roster")
        champs = await self.r.zrevrange(keys.player_champ_games(puuid), 0, top - 1, withscores=True)
        if not champs:
            return []
        wins = await self.r.zmscore(keys.player_champ_wins(puuid), [c for c, _ in champs])
        return [
            {"champion": c, "games": int(g), "wins": int(w or 0), "winrate": round(100 * (w or 0) / g, 1)}
            for (c, g), w in zip(champs, wins)
        ]

    async def roster_champions(self, top: int = 10) -> list[dict]:
        champs = await self.r.zrevrange(keys.roster_champs(), 0, top - 1, withscores=True)
        return [{"champion": c, "games": int(g)} for c, g in champs]

    async def leaderboard(self, board: str, top: int = 10) -> list[dict]:
        if board not in keys.LEADERBOARDS:
            raise NotFound(f"Leaderboard desconocido: {board}")
        rows = await self.r.zrevrange(keys.leaderboard(board), 0, top - 1, withscores=True)
        pipe = self.r.pipeline(transaction=False)
        for puuid, _ in rows:
            pipe.hmget(keys.player(puuid), "gameName", "tagLine", "tier", "rank", "lp", "role")
        details = await pipe.execute()
        out = []
        for pos, ((puuid, score), d) in enumerate(zip(rows, details), start=1):
            name, tag, tier, div, lp, role = d
            out.append({"position": pos, "puuid": puuid, "riotId": f"{name}#{tag}", "role": role,
                        "tier": tier, "rank": div, "lp": int(lp or 0), "score": score})
        return out
