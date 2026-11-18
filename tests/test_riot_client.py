"""The real RiotClient against a mocked HTTP transport (no network)."""
import httpx
import pytest

from app import keys
from app.config import get_settings
from app.errors import RiotRateLimited, UpstreamError
from app.riot.client import RiotClient


def make_client(services, handler):
    settings = get_settings().model_copy(update={"riot_api_key": "RGAPI-test"})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RiotClient(services.redis, settings, http=http)


async def test_routing_headers_and_cache(services):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(200, json={"puuid": "abc", "gameName": "Big Name", "tagLine": "LAN"})

    riot = make_client(services, handler)
    await riot.get_account("Big Name", "LAN")
    await riot.get_account("big name", "lan")  # case-insensitive cache hit
    assert len(seen) == 1
    req = seen[0]
    assert req.url.host == "americas.api.riotgames.com"
    assert req.url.raw_path.decode() == "/riot/account/v1/accounts/by-riot-id/Big%20Name/LAN"
    assert req.headers["X-Riot-Token"] == "RGAPI-test"
    assert await services.redis.ttl(keys.cache("account", "big name#lan")) > 0

    await riot.get_league_entries("abc")
    assert seen[-1].url.host == "la1.api.riotgames.com"
    assert seen[-1].url.path == "/lol/league/v4/entries/by-puuid/abc"

    await riot.get_match_ids("abc", count=5, queue=420)
    assert seen[-1].url.params["queue"] == "420"
    await riot.aclose()


async def test_error_mapping(services):
    riot = make_client(services, lambda req: httpx.Response(403))
    with pytest.raises(UpstreamError, match="expirada"):
        await riot.get_summoner("x")

    riot = make_client(services, lambda req: httpx.Response(429, headers={"Retry-After": "7"}))
    with pytest.raises(RiotRateLimited) as exc:
        await riot.get_summoner("y")
    assert exc.value.retry_after == 7
