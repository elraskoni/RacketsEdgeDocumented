"""
API key enforcement for the public /v1/tennis/* endpoints.

Validates the X-API-Key header against the `api_keys` table (SHA256 hash
match), enforces the org's plan RPM limit via an in-memory sliding window,
and increments daily usage in `usage_records`.

Key generation lives in security.generate_api_key(); this module only
handles validation and enforcement.
"""

import hashlib
import time
from collections import deque
from datetime import date
from threading import Lock
from typing import Any, Dict, Optional

from fastapi import Header, HTTPException, Request

from . import db as dbmod

# ---------------------------------------------------------------------------
# In-memory rate limit windows: org_id -> deque of request timestamps
# ---------------------------------------------------------------------------
_windows: Dict[int, deque] = {}
_windows_lock = Lock()

# Key record cache: key_hash_hex -> (record_dict, cached_at)
# Avoids a DB hit on every request; TTL 30s.
_cache: Dict[str, tuple] = {}
_cache_lock = Lock()
_CACHE_TTL = 30


def _hash_key(raw: str) -> bytes:
    """SHA256 digest matching security.generate_api_key()."""
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _lookup(raw_key: str) -> Optional[Dict[str, Any]]:
    """
    Return the key record (with org plan) from cache or DB.
    Returns None if the key is unknown or revoked.
    """
    key_hash = _hash_key(raw_key)
    cache_key = key_hash.hex()

    with _cache_lock:
        entry = _cache.get(cache_key)
        if entry and time.time() - entry[1] < _CACHE_TTL:
            return entry[0]  # None means "known bad key"

    conn = dbmod.connect()
    try:
        with dbmod.dict_cursor(conn) as cur:
            # Join org → plan to get rpm_limit in one query
            cur.execute(
                """
                SELECT ak.`api_key_id`, ak.`org_id`, ak.`product_id`, ak.`status`,
                       COALESCE(p.`rpm_limit`, 60) AS rpm_limit,
                       COALESCE(p.`plan_id`, 'plan-trial') AS plan_id
                FROM `api_keys` ak
                JOIN `organizations` o ON o.`org_id` = ak.`org_id`
                LEFT JOIN `plans` p ON p.`plan_id` = o.`plan_id`
                WHERE ak.`key_hash` = %s
                LIMIT 1
                """,
                (key_hash,),
            )
            row = cur.fetchone()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not row:
        record = None
    elif row["status"] != "active":
        record = None
    else:
        record = dict(row)

    with _cache_lock:
        _cache[cache_key] = (record, time.time())

    return record


def _enforce_rate_limit(org_id: int, rpm_limit: int) -> None:
    """Raise 429 if the org has exceeded rpm_limit requests in the last 60s."""
    if rpm_limit == 0:
        return  # 0 = unlimited (Enterprise)

    now = time.time()
    window_start = now - 60.0

    with _windows_lock:
        if org_id not in _windows:
            _windows[org_id] = deque()
        w = _windows[org_id]
        while w and w[0] < window_start:
            w.popleft()
        if len(w) >= rpm_limit:
            raise HTTPException(
                status_code=429,
                detail=f"rate_limit_exceeded ({rpm_limit} req/min)",
                headers={"Retry-After": "60"},
            )
        w.append(now)


def _touch_last_used(api_key_id: int) -> None:
    """Update last_used_at on the key. Fire-and-forget; errors silently ignored."""
    try:
        conn = dbmod.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE `api_keys` SET `last_used_at`=NOW() WHERE `api_key_id`=%s",
                    (api_key_id,),
                )
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:
        pass


def _increment_usage(org_id: int, product_id: str, is_error: bool = False) -> None:
    """Upsert today's usage row. Fire-and-forget; errors silently ignored."""
    try:
        conn = dbmod.connect()
        try:
            with conn.cursor() as cur:
                today = date.today().isoformat()
                if is_error:
                    cur.execute(
                        """
                        INSERT INTO `usage_records` (`org_id`, `product_id`, `category`, `date`, `requests`, `errors`)
                        VALUES (%s, %s, 'api', %s, 1, 1)
                        ON DUPLICATE KEY UPDATE `requests`=`requests`+1, `errors`=`errors`+1
                        """,
                        (org_id, product_id, today),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO `usage_records` (`org_id`, `product_id`, `category`, `date`, `requests`, `errors`)
                        VALUES (%s, %s, 'api', %s, 1, 0)
                        ON DUPLICATE KEY UPDATE `requests`=`requests`+1
                        """,
                        (org_id, product_id, today),
                    )
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:
        pass


def require_api_key(request: Request, x_api_key: str = Header(...)) -> Dict[str, Any]:
    """
    FastAPI dependency. Inject into any route that requires authentication:

        @app.get("/v1/tennis/today")
        def get_today(key=Depends(require_api_key)):
            ...

    Returns the key record dict (keys: api_key_id, org_id, product_id, rpm_limit, plan_id).
    Raises 401 for invalid/revoked keys, 429 for rate limit breach.
    """
    record = _lookup(x_api_key)
    if record is None:
        raise HTTPException(status_code=401, detail="invalid_api_key")

    _enforce_rate_limit(int(record["org_id"]), int(record["rpm_limit"]))
    _touch_last_used(int(record["api_key_id"]))
    _increment_usage(int(record["org_id"]), str(record["product_id"]))

    return record
