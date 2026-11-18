"""Single source of truth for the Redis key namespace.

Convention:  <prefix>:<entity>:<id>[:<sub-entity>]
Every key used by the app is built here, so the documentation, the seeding
script and the code can never drift apart.

| Key                                   | Type       | TTL        |
|---------------------------------------|------------|------------|
| rift:player:{puuid}                   | Hash       | none       |
| rift:idx:riotid:{name#tag}            | String     | none       |
| rift:roster                           | Set        | none       |
| rift:player:{puuid}:matches           | List       | none (cap) |
| rift:player:{puuid}:seen              | Set        | none       |
| rift:player:{puuid}:totals            | Hash       | none       |
| rift:player:{puuid}:champs:games      | Sorted Set | none       |
| rift:player:{puuid}:champs:wins       | Sorted Set | none       |
| rift:match:{matchId}                  | Hash       | none       |
| rift:lb:{rank|winrate|kda}            | Sorted Set | none       |
| rift:champs:roster                    | Sorted Set | none       |
| rift:cache:{kind}:{id}                | String     | yes        |
| rift:rl:riot:{window}:{bucket}        | String     | yes        |
| rift:lock:sync:{puuid}                | String     | yes        |
| rift:cooldown:sync:{puuid}            | String     | yes        |
| rift:stats:cache                      | Hash       | none       |
| rift:recent:searches                  | List       | none (cap) |
"""
from __future__ import annotations

from .config import get_settings

P = get_settings().key_prefix

LEADERBOARDS = ("rank", "winrate", "kda")


def normalize_riot_id(game_name: str, tag_line: str) -> str:
    return f"{game_name.strip().lower()}#{tag_line.strip().lower()}"


# --- persistent domain data -------------------------------------------------
def player(puuid: str) -> str:
    return f"{P}:player:{puuid}"


def riot_id_index(game_name: str, tag_line: str) -> str:
    return f"{P}:idx:riotid:{normalize_riot_id(game_name, tag_line)}"


def roster() -> str:
    return f"{P}:roster"


def player_matches(puuid: str) -> str:
    return f"{P}:player:{puuid}:matches"


def player_seen(puuid: str) -> str:
    return f"{P}:player:{puuid}:seen"


def player_totals(puuid: str) -> str:
    return f"{P}:player:{puuid}:totals"


def player_champ_games(puuid: str) -> str:
    return f"{P}:player:{puuid}:champs:games"


def player_champ_wins(puuid: str) -> str:
    return f"{P}:player:{puuid}:champs:wins"


def match(match_id: str) -> str:
    return f"{P}:match:{match_id}"


def leaderboard(board: str) -> str:
    return f"{P}:lb:{board}"


def roster_champs() -> str:
    return f"{P}:champs:roster"


def recent_searches() -> str:
    return f"{P}:recent:searches"


def cache_stats() -> str:
    return f"{P}:stats:cache"


# --- volatile data (always with TTL) ----------------------------------------
def cache(kind: str, ident: str) -> str:
    return f"{P}:cache:{kind}:{ident}"


def cache_pattern() -> str:
    return f"{P}:cache:*"


def rate_limit(window: str, bucket: int) -> str:
    return f"{P}:rl:riot:{window}:{bucket}"


def sync_lock(puuid: str) -> str:
    return f"{P}:lock:sync:{puuid}"


def sync_cooldown(puuid: str) -> str:
    return f"{P}:cooldown:sync:{puuid}"


def all_keys_pattern() -> str:
    return f"{P}:*"


VOLATILE_PATTERNS = (f"{P}:cache:*", f"{P}:rl:*", f"{P}:lock:*", f"{P}:cooldown:*")
