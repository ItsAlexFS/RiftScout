"""Test-only Riot API backed by tests/fixtures/riot_data.json.

Same interface and same Redis behaviour (cache, TTL, rate limiter) as the real
client: only the HTTP call is replaced. The application never uses it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from app.errors import RiotNotFound
from app.riot.client import RiotClient

_ROUTES = [
    ("account", re.compile(r"^/riot/account/v1/accounts/by-riot-id/([^/]+)/([^/]+)$")),
    ("summoner", re.compile(r"^/lol/summoner/v4/summoners/by-puuid/([^/]+)$")),
    ("league", re.compile(r"^/lol/league/v4/entries/by-puuid/([^/]+)$")),
    ("matchids", re.compile(r"^/lol/match/v5/matches/by-puuid/([^/]+)/ids$")),
    ("match", re.compile(r"^/lol/match/v5/matches/([^/]+)$")),
]


DATA_PATH = Path(__file__).resolve().parent / "fixtures" / "riot_data.json"


class FakeRiotClient(RiotClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        self._accounts = {
            f"{a['gameName'].lower()}#{a['tagLine'].lower()}": a for a in self.data["players"]
        }
        self._by_puuid = {a["puuid"]: a for a in self.data["players"]}

    async def _fetch(self, host: str, path: str, params: dict | None = None) -> Any:
        for name, rx in _ROUTES:
            m = rx.match(path)
            if not m:
                continue
            if name == "account":
                acc = self._accounts.get(f"{unquote(m[1]).lower()}#{unquote(m[2]).lower()}")
                if not acc:
                    raise RiotNotFound("Riot ID no encontrado (fake)")
                return {"puuid": acc["puuid"], "gameName": acc["gameName"], "tagLine": acc["tagLine"]}
            player = self._by_puuid.get(m[1])
            if name in ("summoner", "league", "matchids") and not player:
                raise RiotNotFound("PUUID no encontrado (fake)")
            if name == "summoner":
                return {"puuid": player["puuid"], **player["summoner"]}
            if name == "league":
                return player["league"]
            if name == "matchids":
                params = params or {}
                ids = player["matchIds"]
                if params.get("queue"):
                    ids = [i for i in ids if self.data["matches"][i]["info"]["queueId"] == params["queue"]]
                start = int(params.get("start", 0))
                return ids[start:start + int(params.get("count", 20))]
            if name == "match":
                match = self.data["matches"].get(m[1])
                if not match:
                    raise RiotNotFound("Partida no encontrada (fake)")
                return match
        raise RiotNotFound(f"Ruta no soportada por el fake: {path}")
