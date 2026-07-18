"""The Redis cache must degrade to a safe no-op when Redis isn't configured."""
import pytest

from app import cache

pytestmark = pytest.mark.asyncio


async def test_cache_disabled_is_noop():
    # No REDIS_URL / no init_cache() call → disabled.
    assert cache.enabled() is False
    # Reads miss, writes and invalidations are silent no-ops (never raise).
    assert await cache.get_json("chargers:nearby:x") is None
    await cache.set_json("chargers:nearby:x", [{"id": "1"}], ttl=5)
    assert await cache.get_json("chargers:nearby:x") is None
    await cache.delete_prefix(cache.NEARBY_PREFIX)


async def test_init_cache_without_url_stays_disabled():
    # init with empty settings.redis_url must not raise and must stay disabled.
    await cache.init_cache()
    assert cache.enabled() is False
    await cache.close_cache()
