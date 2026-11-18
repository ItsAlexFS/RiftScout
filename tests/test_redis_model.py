import pytest

from app import keys
from app.errors import Conflict, NotFound, RiotNotFound, TooManyRequests
from app.services.ranking import rank_score



async def _add(services, name="Kukulcan"):
    return await services.players.create(name, "MYB", role="MID")


# ------------------------------------------------------------------- CRUD
async def test_create_read_update_delete(services):
    r = services.redis
    p = await _add(services)
    puuid = p["puuid"]
    assert await r.type(keys.player(puuid)) == "hash"
    assert await r.sismember(keys.roster(), puuid)
    assert await r.get(keys.riot_id_index("kukulcan", "myb")) == puuid
    assert await r.zscore(keys.leaderboard("rank"), puuid) == rank_score("DIAMOND", "II", 54)

    with pytest.raises(Conflict):
        await _add(services)

    updated = await services.players.update(puuid, {"notes": "main Ahri", "role": "MID", "tier": "HACK"})
    assert updated["notes"] == "main Ahri"
    assert updated["tier"] == "DIAMOND"  # non editable fields are ignored

    await services.sync.sync_player(puuid, count=10, force=True)
    await services.players.delete(puuid)
    leftovers = [k async for k in r.scan_iter(match=f"{keys.player(puuid)}*")]
    assert leftovers == []
    assert not await r.sismember(keys.roster(), puuid)
    for board in keys.LEADERBOARDS:
        assert await r.zscore(keys.leaderboard(board), puuid) is None
    with pytest.raises(NotFound):
        await services.players.get(puuid)


async def test_unknown_riot_id_is_negatively_cached(services):
    with pytest.raises(RiotNotFound):
        await services.players.create("Nadie", "LAN")
    ttl = await services.redis.ttl(keys.cache("account", "nadie#lan"))
    assert 0 < ttl <= services.riot.s.ttl_negative
    with pytest.raises(RiotNotFound):  # served from cache this time
        await services.players.create("Nadie", "LAN")
    assert int(await services.redis.hget(keys.cache_stats(), "hits")) >= 1


# ---------------------------------------------------------- TTL patterns
async def test_cache_keys_have_expected_ttl(services):
    p = await _add(services)
    s = services.riot.s
    r = services.redis
    assert 0 < await r.ttl(keys.cache("account", "kukulcan#myb")) <= s.ttl_account
    assert 0 < await r.ttl(keys.cache("summoner", p["puuid"])) <= s.ttl_summoner
    assert 0 < await r.ttl(keys.cache("league", p["puuid"])) <= s.ttl_league
    assert await r.ttl(keys.player(p["puuid"])) == -1  # domain data never expires


async def test_sync_cooldown_and_lock(services):
    p = await _add(services)
    await services.sync.sync_player(p["puuid"], count=5)
    assert 0 < await services.redis.ttl(keys.sync_cooldown(p["puuid"])) <= 60
    assert await services.redis.exists(keys.sync_lock(p["puuid"])) == 0  # released via Lua
    with pytest.raises(TooManyRequests):
        await services.sync.sync_player(p["puuid"], count=5)

    await services.redis.set(keys.sync_lock(p["puuid"]), "other-worker", ex=30)
    with pytest.raises(Conflict):
        await services.sync.sync_player(p["puuid"], count=5, force=True)


# -------------------------------------------------------- data structures
async def test_sync_is_idempotent_and_shares_matches(services):
    a = await _add(services, "Kukulcan")
    first = await services.sync.sync_player(a["puuid"], count=15, force=True)
    again = await services.sync.sync_player(a["puuid"], count=15, force=True)
    assert again["newMatches"] == 0
    games = int(await services.redis.hget(keys.player_totals(a["puuid"]), "games"))
    assert games == first["ingested"]
    assert await services.redis.llen(keys.player_matches(a["puuid"])) == games

    b = await _add(services, "Ixchel")  # plays some games together with Kukulcan
    res = await services.sync.sync_player(b["puuid"], count=15, force=True)
    assert res["reusedFromRedis"] > 0  # shared matches were not downloaded twice


async def test_champion_pool_and_leaderboards(services):
    for name in ("Kukulcan", "Chaac", "Itzamna"):
        p = await _add(services, name)
        await services.sync.sync_player(p["puuid"], count=10, force=True)

    pool = await services.stats.champion_pool(p["puuid"], top=3)
    assert pool and pool[0]["games"] >= pool[-1]["games"]
    total = sum(int(s) for _, s in await services.redis.zrange(keys.player_champ_games(p["puuid"]), 0, -1, withscores=True))
    assert total == int(await services.redis.hget(keys.player_totals(p["puuid"]), "games"))

    rank = await services.stats.leaderboard("rank")
    assert [r["riotId"] for r in rank] == ["Kukulcan#MYB", "Itzamna#MYB", "Chaac#MYB"]
    kda = await services.stats.leaderboard("kda")
    assert kda == sorted(kda, key=lambda r: r["score"], reverse=True)

    matches = await services.stats.player_matches(p["puuid"], limit=5)
    assert len(matches) == 5
    assert matches == sorted(matches, key=lambda m: m["gameCreation"], reverse=True)


async def test_rate_limiter_counts_requests(services):
    await _add(services)
    snap = await services.riot.limiter.snapshot()
    assert snap[1]["used"] >= 3  # account + summoner + league
    for w in snap:
        assert 0 < await services.redis.ttl(w["key"]) <= w["window_s"] + 1


def test_rank_score_ordering():
    assert rank_score("GOLD", "I", 99) < rank_score("PLATINUM", "IV", 0)
    assert rank_score("DIAMOND", "I", 99) < rank_score("MASTER", "I", 0)
    assert rank_score("CHALLENGER", "I", 1200) > rank_score("MASTER", "I", 300)
    assert rank_score(None, None, None) is None
