"""
Endpoint feature flags — allow admins to enable/disable individual API
endpoints at runtime without redeploying.

Flags are stored in the `endpoint_flags` DB table and cached in memory for
30 seconds so the check adds no perceptible latency to requests.

Usage in app_tennis.py:
    from .endpoint_flags import endpoint_guard

    @app.get("/v1/tennis/today", dependencies=[Depends(endpoint_guard("today"))])
    def get_today(...):
        ...
"""

import time
from threading import Lock
from typing import Dict, Optional, Tuple

from fastapi import HTTPException

from . import db as dbmod

# All endpoint keys the API exposes. Seeded into DB on first run.
KNOWN_ENDPOINTS = [
    "today",
    "live",
    "date",
    "leaderboard",
    "player_profile",
    "player_matches",
    "pbp",
    "h2h",
    "predict",
    "player_search",
    "doubles",
]

_CACHE_TTL = 30  # seconds

# Cache: {endpoint_key: (enabled, reason, cached_at)}
_cache: Dict[str, Tuple[bool, Optional[str], float]] = {}
_lock = Lock()
_table_ready = False


def _ensure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    conn = dbmod.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS `endpoint_flags` (
                    `endpoint_key` VARCHAR(64) NOT NULL,
                    `enabled`      TINYINT(1)  NOT NULL DEFAULT 1,
                    `reason`       VARCHAR(255)         DEFAULT NULL,
                    `updated_at`   TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
                                               ON UPDATE CURRENT_TIMESTAMP,
                    `updated_by`   VARCHAR(128)         DEFAULT NULL,
                    PRIMARY KEY (`endpoint_key`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            # Seed any missing endpoints as enabled
            for key in KNOWN_ENDPOINTS:
                cur.execute("""
                    INSERT IGNORE INTO `endpoint_flags`
                        (`endpoint_key`, `enabled`)
                    VALUES (%s, 1)
                """, (key,))
        _table_ready = True
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _fetch_from_db(key: str) -> Tuple[bool, Optional[str]]:
    conn = dbmod.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `enabled`, `reason` FROM `endpoint_flags` WHERE `endpoint_key`=%s",
                (key,),
            )
            row = cur.fetchone()
            if row is None:
                return True, None  # unknown keys default to enabled
            enabled = bool(row[0])
            reason = row[1] if len(row) > 1 else None
            return enabled, reason
    finally:
        try:
            conn.close()
        except Exception:
            pass


def is_enabled(key: str) -> Tuple[bool, Optional[str]]:
    """Return (enabled, reason) for an endpoint key, using the in-memory cache."""
    _ensure_table()
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached and (now - cached[2]) < _CACHE_TTL:
            return cached[0], cached[1]

    enabled, reason = _fetch_from_db(key)
    with _lock:
        _cache[key] = (enabled, reason, time.monotonic())
    return enabled, reason


def invalidate(key: str) -> None:
    """Force the next request to re-read this key from the DB."""
    with _lock:
        _cache.pop(key, None)


def endpoint_guard(key: str):
    """
    FastAPI dependency factory. Returns a callable that raises 503 when the
    endpoint is disabled.

    Usage:
        @app.get("/v1/tennis/today", dependencies=[Depends(endpoint_guard("today"))])
    """
    def check():
        enabled, reason = is_enabled(key)
        if not enabled:
            detail = {"error": "endpoint_disabled", "endpoint": key}
            if reason:
                detail["reason"] = reason
            raise HTTPException(status_code=503, detail=detail)

    return check
