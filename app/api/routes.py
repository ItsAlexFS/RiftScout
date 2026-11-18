"""REST endpoints. Handlers stay thin: all Redis logic lives in app.services."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from .schemas import PlayerCreate, PlayerUpdate

router = APIRouter(prefix="/api")


def _svc(request: Request):
    return request.app.state.services


# ------------------------------------------------------------------ players
@router.get("/players", tags=["players"])
async def list_players(request: Request):
    return await _svc(request).players.list_all()


@router.post("/players", status_code=201, tags=["players"])
async def create_player(body: PlayerCreate, request: Request):
    return await _svc(request).players.create(body.gameName, body.tagLine, body.role, body.notes)


@router.get("/players/by-riot-id/{game_name}/{tag_line}", tags=["players"])
async def get_player_by_riot_id(game_name: str, tag_line: str, request: Request):
    return await _svc(request).players.get_by_riot_id(game_name, tag_line)


@router.get("/players/{puuid}", tags=["players"])
async def get_player(puuid: str, request: Request):
    return await _svc(request).players.get(puuid)


@router.patch("/players/{puuid}", tags=["players"])
async def update_player(puuid: str, body: PlayerUpdate, request: Request):
    return await _svc(request).players.update(puuid, body.model_dump(exclude_none=True))


@router.delete("/players/{puuid}", tags=["players"])
async def delete_player(puuid: str, request: Request):
    return await _svc(request).players.delete(puuid)


@router.post("/players/{puuid}/sync", tags=["players"])
async def sync_player(puuid: str, request: Request, count: int = Query(10, ge=1, le=20)):
    return await _svc(request).sync.sync_player(puuid, count=count)


@router.get("/players/{puuid}/matches", tags=["stats"])
async def player_matches(puuid: str, request: Request, limit: int = Query(10, ge=1, le=50)):
    return await _svc(request).stats.player_matches(puuid, limit)


@router.get("/players/{puuid}/champions", tags=["stats"])
async def player_champions(puuid: str, request: Request, top: int = Query(5, ge=1, le=20)):
    return await _svc(request).stats.champion_pool(puuid, top)


# -------------------------------------------------------------------- stats
@router.get("/matches/{match_id}", tags=["stats"])
async def match_detail(match_id: str, request: Request):
    return await _svc(request).stats.match_detail(match_id)


@router.get("/leaderboards/{board}", tags=["stats"])
async def leaderboard(board: str, request: Request, top: int = Query(10, ge=1, le=50)):
    return await _svc(request).stats.leaderboard(board, top)


@router.get("/champions/top", tags=["stats"])
async def roster_champions(request: Request, top: int = Query(10, ge=1, le=50)):
    return await _svc(request).stats.roster_champions(top)


# ------------------------------------------------------------------- system
@router.get("/system/health", tags=["system"])
async def health(request: Request):
    return await _svc(request).system.health()


@router.get("/system/keys", tags=["system"])
async def volatile_keys(request: Request):
    return await _svc(request).system.volatile_keys()


@router.get("/system/cache", tags=["system"])
async def cache_stats(request: Request):
    return await _svc(request).system.cache_stats()


@router.delete("/system/cache", tags=["system"])
async def purge_cache(request: Request):
    return {"removed": await _svc(request).system.purge_cache()}
