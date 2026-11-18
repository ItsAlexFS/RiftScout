"""Pure helpers to turn Riot payloads into flat values that fit in Redis."""
from __future__ import annotations

import json

TIERS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND"]
APEX = ["MASTER", "GRANDMASTER", "CHALLENGER"]
DIVISIONS = {"IV": 0, "III": 1, "II": 2, "I": 3}
# Riot's teamPosition names -> the role names used by the app (UTILITY is support).
POSITION_NAMES = {"UTILITY": "SUPPORT", "MIDDLE": "MID", "BOTTOM": "ADC"}


def position_name(position: str) -> str:
    return POSITION_NAMES.get(position, position)


def rank_score(tier: str | None, division: str | None, lp: int | str | None) -> float | None:
    """Single sortable number for the rank leaderboard (Sorted Set score).

    IRON IV 0 LP = 0, each division = 100, each tier = 400.
    Master+ share one base (2800) because their LP is continuous.
    """
    if not tier:
        return None
    lp = int(lp or 0)
    tier = tier.upper()
    if tier in APEX:
        return 2800 + lp
    if tier not in TIERS:
        return None
    return TIERS.index(tier) * 400 + DIVISIONS.get((division or "IV").upper(), 0) * 100 + lp


def solo_queue_entry(entries: list[dict]) -> dict | None:
    return next((e for e in entries if e.get("queueType") == "RANKED_SOLO_5x5"), None)


def kda(kills: int, deaths: int, assists: int) -> float:
    return round((kills + assists) / max(1, deaths), 2)


def compact_match(dto: dict) -> dict[str, str]:
    """Reduce a ~40 KB match-v5 payload to ~2 KB of fields we really use.

    Stored as a Hash; the 10 participants go in one JSON field because they are
    always read together with the match (no partial access needed).
    """
    info = dto["info"]
    duration = int(info.get("gameDuration", 0))
    if "gameEndTimestamp" not in info and duration > 10_000:  # very old matches report ms
        duration //= 1000
    winning = next((t["teamId"] for t in info.get("teams", []) if t.get("win")), 0)
    participants = [
        {
            "puuid": p["puuid"],
            "riotId": f"{p.get('riotIdGameName', '?')}#{p.get('riotIdTagline', '?')}",
            "champion": p["championName"],
            "team": p["teamId"],
            "position": position_name(p.get("teamPosition") or p.get("individualPosition") or ""),
            "k": p["kills"], "d": p["deaths"], "a": p["assists"],
            "win": bool(p["win"]),
            "cs": p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0),
            "gold": p.get("goldEarned", 0),
            "dmg": p.get("totalDamageDealtToChampions", 0),
            "vision": p.get("visionScore", 0),
            "remake": bool(p.get("gameEndedInEarlySurrender", False)),
        }
        for p in info["participants"]
    ]
    return {
        "matchId": dto["metadata"]["matchId"],
        "queueId": str(info.get("queueId", 0)),
        "gameCreation": str(info.get("gameCreation", 0)),
        "gameDuration": str(duration),
        "gameVersion": info.get("gameVersion", ""),
        "platformId": info.get("platformId", ""),
        "winningTeam": str(winning),
        "participants": json.dumps(participants, separators=(",", ":")),
    }


def participant(match_hash: dict, puuid: str) -> dict | None:
    for p in json.loads(match_hash.get("participants", "[]")):
        if p["puuid"] == puuid:
            return p
    return None
