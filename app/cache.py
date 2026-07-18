"""Redis read-through cache with graceful degradation.

If ``REDIS_URL`` is unset or Redis is unreachable, every function here becomes a
safe no-op — the app keeps working (just without caching), so local dev and the
test suite don't need Redis running. Connections are managed by the app lifespan
(``init_cache`` / ``close_cache`` in ``main.py``).
"""
import json
import logging
from typing import Any, Optional

from .config import settings

log = logging.getLogger("amplocate.cache")

_client = None
_enabled = False


async def init_cache() -> None:
    global _client, _enabled
    if not settings.redis_url:
        _enabled = False
        log.info("REDIS_URL not set — response caching disabled.")
        return
    try:
        import redis.asyncio as aioredis

        _client = aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        await _client.ping()
        _enabled = True
        log.info("Redis cache enabled (%s).", settings.redis_url.split("@")[-1])
    except Exception as e:  # noqa: BLE001 — never let cache setup break startup
        _client, _enabled = None, False
        log.warning("Redis unavailable — caching disabled: %s", e)


async def close_cache() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001
            pass
    _client = None


def enabled() -> bool:
    return _enabled and _client is not None


async def get_json(key: str) -> Optional[Any]:
    if not enabled():
        return None
    try:
        raw = await _client.get(key)
        return json.loads(raw) if raw else None
    except Exception as e:  # noqa: BLE001
        log.warning("cache get(%s) failed: %s", key, e)
        return None


async def set_json(key: str, value: Any, ttl: Optional[int] = None) -> None:
    if not enabled():
        return
    try:
        await _client.set(key, json.dumps(value, default=str), ex=ttl or settings.cache_ttl_seconds)
    except Exception as e:  # noqa: BLE001
        log.warning("cache set(%s) failed: %s", key, e)


async def delete_prefix(prefix: str) -> None:
    """Invalidate every key under a prefix (used when reliability changes)."""
    if not enabled():
        return
    try:
        async for k in _client.scan_iter(match=f"{prefix}*", count=250):
            await _client.delete(k)
    except Exception as e:  # noqa: BLE001
        log.warning("cache delete_prefix(%s) failed: %s", prefix, e)


NEARBY_PREFIX = "chargers:nearby:"
