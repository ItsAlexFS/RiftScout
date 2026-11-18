"""Application settings loaded from environment variables (.env supported).

Credentials (Redis URL, Riot API key) are never hard-coded: they come from the
environment so the same code runs locally, in CI and during the live demo.
"""
from __future__ import annotations

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Redis Cloud -------------------------------------------------------
    # Example: redis://default:<password>@redis-12345.c123.us-east-1-2.ec2.redns.redis-cloud.com:12345
    # Use rediss:// if TLS is enabled on the database.
    redis_url: str = Field(..., description="Connection URL of the Redis Cloud database")
    redis_max_connections: int = 10  # Redis Cloud free tier allows 30 connections in total
    key_prefix: str = "rift"

    # --- Riot Games API ----------------------------------------------------
    riot_api_key: str = ""
    riot_platform: str = "la1"      # LAN. Platform routing: summoner-v4, league-v4
    riot_region: str = "americas"   # Regional routing: account-v1, match-v5
    match_queue: int = 420          # 420 = Ranked Solo/Duo. 0 = any queue

    # Riot dev/personal key limits are 20 req/1s and 100 req/2min.
    # We stay slightly below them on purpose.
    riot_limit_per_second: int = 19
    riot_limit_per_2min: int = 95

    # --- TTL policy (seconds) ---------------------------------------------
    ttl_account: int = 86_400       # Riot ID -> PUUID barely changes
    ttl_summoner: int = 3_600       # level / icon
    ttl_league: int = 600           # rank and LP change after every game
    ttl_match_ids: int = 120        # list of latest matches
    ttl_negative: int = 60          # "player not found" answers
    sync_cooldown: int = 60         # min seconds between syncs of the same player
    sync_lock_ttl: int = 30         # safety expiry for the distributed lock
    recent_matches_cap: int = 100   # LTRIM size for each player's match list


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
