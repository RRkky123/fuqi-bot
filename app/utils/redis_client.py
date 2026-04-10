"""
Redis 連線（開發模式自動用 fakeredis，正式環境用真實 Redis）
"""
import json
from typing import Any, Optional

from app.config import get_settings

settings = get_settings()

_redis = None


async def get_redis():
    global _redis
    if _redis is not None:
        return _redis

    try:
        import redis.asyncio as aioredis
        r = await aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
        )
        await r.ping()  # 測試連線
        _redis = r
    except Exception:
        # 連不上 → 用 fakeredis（開發模式）
        import fakeredis.aioredis as fakeredis
        _redis = fakeredis.FakeRedis(decode_responses=True)

    return _redis


# ── 使用者對話狀態 ────────────────────────────────────────────
USER_STATE_TTL = 60 * 30


class UserState:
    IDLE             = "idle"
    WAITING_AVATAR   = "waiting_avatar"
    WAITING_LOCATION = "waiting_location"
    WAITING_CITY     = "waiting_city"
    WAITING_THEME    = "waiting_theme"
    PROCESSING       = "processing"


async def get_user_state(line_uid: str) -> str:
    r = await get_redis()
    state = await r.get(f"state:{line_uid}")
    return state or UserState.IDLE


async def set_user_state(line_uid: str, state: str, extra: dict = None) -> None:
    r = await get_redis()
    await r.set(f"state:{line_uid}", state, ex=USER_STATE_TTL)
    if extra:
        await r.set(f"state_extra:{line_uid}", json.dumps(extra, ensure_ascii=False), ex=USER_STATE_TTL)


async def get_user_state_extra(line_uid: str) -> dict:
    r = await get_redis()
    raw = await r.get(f"state_extra:{line_uid}")
    return json.loads(raw) if raw else {}


async def clear_user_state(line_uid: str) -> None:
    r = await get_redis()
    await r.delete(f"state:{line_uid}", f"state_extra:{line_uid}")


# ── 天氣快取 ─────────────────────────────────────────────────
WEATHER_CACHE_TTL = 60 * 30


async def cache_weather(location_key: str, data: dict) -> None:
    r = await get_redis()
    await r.set(f"weather:{location_key}", json.dumps(data, ensure_ascii=False), ex=WEATHER_CACHE_TTL)


async def get_cached_weather(location_key: str) -> Optional[dict]:
    r = await get_redis()
    raw = await r.get(f"weather:{location_key}")
    return json.loads(raw) if raw else None


# ── 每日合成計數 ──────────────────────────────────────────────
async def increment_daily_synthesis_count() -> int:
    from datetime import date
    r = await get_redis()
    key = f"daily_synthesis:{date.today().isoformat()}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, 86400)
    return count


async def get_daily_synthesis_count() -> int:
    from datetime import date
    r = await get_redis()
    key = f"daily_synthesis:{date.today().isoformat()}"
    val = await r.get(key)
    return int(val) if val else 0


# ── 防刷：每日邀請計數 ────────────────────────────────────────
async def increment_daily_referral_count(inviter_uid: str) -> int:
    from datetime import date
    r = await get_redis()
    key = f"daily_referral:{date.today().isoformat()}:{inviter_uid}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, 86400)
    return count
