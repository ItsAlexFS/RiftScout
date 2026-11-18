"""Riot Games API client with a Redis cache-aside layer.

Flow of every cached call:
    GET rift:cache:{kind}:{id}  -> hit: return it (no HTTP request)
                                -> miss: rate-limit, call Riot, SET ... EX ttl
Routing (LAN):
    regional  americas.api.riotgames.com   account-v1, match-v5
    platform  la1.api.riotgames.com        summoner-v4, league-v4
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx
from redis.asyncio import Redis

from .. import keys
from ..config import Settings
from ..errors import RiotNotFound, RiotRateLimited, UpstreamError
from .ratelimit import RiotRateLimiter

_NOT_FOUND = {"__not_found__": True}


class RiotClient:
    def __init__(self, redis: Redis, settings: Settings, http: httpx.AsyncClient | None = None):
        self.r = redis
        self.s = settings
        self.http = http or httpx.AsyncClient(timeout=10)
        self.limiter = RiotRateLimiter(redis, settings)

    async def aclose(self) -> None:
        await self.http.aclose()

    # ------------------------------------------------------------------ hosts
    @property
    def regional_host(self) -> str:
        return f"{self.s.riot_region}.api.riotgames.com"

    @property
    def platform_host(self) -> str:
        return f"{self.s.riot_platform}.api.riotgames.com"

    # ------------------------------------------------------------- low level
    async def _fetch(self, host: str, path: str, params: dict | None = None) -> Any:
        """Real HTTP request (tests override only this method)."""
        if not self.s.riot_api_key:
            raise UpstreamError("RIOT_API_KEY no está configurada")
        try:
            resp = await self.http.get(
                f"https://{host}{path}", params=params,
                headers={"X-Riot-Token": self.s.riot_api_key},
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Error de red con la API de Riot: {exc}") from exc

        if resp.status_code == 404:
            raise RiotNotFound("Riot no encontró el recurso solicitado")
        if resp.status_code == 429:
            raise RiotRateLimited("Riot respondió 429", retry_after=int(resp.headers.get("Retry-After", "1")))
        if resp.status_code in (401, 403):
            raise UpstreamError("API key de Riot inválida o expirada (las dev keys duran 24 h)")
        if resp.status_code >= 400:
            raise UpstreamError(f"Riot respondió {resp.status_code}")
        return resp.json()

    async def _request(self, host: str, path: str, params: dict | None = None) -> Any:
        await self.limiter.acquire()
        await self.r.hincrby(keys.cache_stats(), "riot_calls", 1)
        return await self._fetch(host, path, params)

    async def _cached(self, kind: str, ident: str, ttl: int,
                      loader: Callable[[], Awaitable[Any]], negative: bool = False) -> Any:
        key = keys.cache(kind, ident)
        raw = await self.r.get(key)
        if raw is not None:
            await self.r.hincrby(keys.cache_stats(), "hits", 1)
            data = json.loads(raw)
            if data == _NOT_FOUND:
                raise RiotNotFound("Jugador no encontrado (respuesta en caché)")
            return data

        await self.r.hincrby(keys.cache_stats(), "misses", 1)
        try:
            data = await loader()
        except RiotNotFound:
            if negative:  # negative caching: avoid hammering Riot with typos
                await self.r.set(key, json.dumps(_NOT_FOUND), ex=self.s.ttl_negative)
            raise
        await self.r.set(key, json.dumps(data, separators=(",", ":")), ex=ttl)
        return data

    # ------------------------------------------------------------ endpoints
    async def get_account(self, game_name: str, tag_line: str) -> dict:
        """ACCOUNT-V1: Riot ID -> {puuid, gameName, tagLine}."""
        path = f"/riot/account/v1/accounts/by-riot-id/{quote(game_name)}/{quote(tag_line)}"
        return await self._cached(
            "account", keys.normalize_riot_id(game_name, tag_line), self.s.ttl_account,
            lambda: self._request(self.regional_host, path), negative=True,
        )

    async def get_summoner(self, puuid: str) -> dict:
        """SUMMONER-V4: level and profile icon."""
        path = f"/lol/summoner/v4/summoners/by-puuid/{puuid}"
        return await self._cached("summoner", puuid, self.s.ttl_summoner,
                                  lambda: self._request(self.platform_host, path))

    async def get_league_entries(self, puuid: str) -> list[dict]:
        """LEAGUE-V4: ranked entries (tier, division, LP) by PUUID."""
        path = f"/lol/league/v4/entries/by-puuid/{puuid}"
        return await self._cached("league", puuid, self.s.ttl_league,
                                  lambda: self._request(self.platform_host, path))

    async def get_match_ids(self, puuid: str, count: int = 10, queue: int = 0) -> list[str]:
        """MATCH-V5: latest match ids (newest first)."""
        params: dict[str, int] = {"start": 0, "count": count}
        if queue:
            params["queue"] = queue
        path = f"/lol/match/v5/matches/by-puuid/{puuid}/ids"
        return await self._cached("matchids", f"{puuid}:{queue}:{count}", self.s.ttl_match_ids,
                                  lambda: self._request(self.regional_host, path, params))

    async def get_match(self, match_id: str) -> dict:
        """MATCH-V5: full match. Not cached here: matches are immutable and are
        persisted in compact form as rift:match:{id} by the sync service."""
        return await self._request(self.regional_host, f"/lol/match/v5/matches/{match_id}")
