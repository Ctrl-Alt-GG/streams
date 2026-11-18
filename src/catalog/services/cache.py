import logging
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import LockError
from redis.exceptions import TimeoutError as RedisTimeoutError

logger = logging.getLogger(__name__)

POLL_LOCK_KEY = "catalog:mediamtx:poll-lock:v1"


def _sanitize_for_log(value: object) -> str:
    return str(value).replace("\r", "").replace("\n", "")


def read_public_cache(key: str) -> object:
    try:
        return cache.get(key)
    except (RedisConnectionError, RedisTimeoutError) as error:
        logger.warning("catalog_cache_read_failed key=%s error=%s", _sanitize_for_log(key), error)
        return None


@contextmanager
def cache_lock(key: str, timeout: float) -> Iterator[bool]:
    """
    Hold `key` for at most `timeout` seconds, without waiting: yields whether this process got
    it. Releasing a lock that expired meanwhile is not an error, as the work is done.

    django-redis locks atomically in Redis, and Redis errors propagate, so that nobody works
    without the lock. The in-memory cache of the tests has no locks, hence the `add` below.
    """
    lock_factory = getattr(cache, "lock", None)
    if lock_factory is not None:
        lock = lock_factory(key, timeout=timeout, blocking_timeout=0)
        acquired = lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                with suppress(LockError):
                    lock.release()
        return

    token = uuid4().hex
    acquired = cache.add(key, token, timeout=timeout)
    try:
        yield acquired
    finally:
        if acquired and cache.get(key) == token:
            cache.delete(key)


def reconcile_lock():
    """
    Held by a check of the media server, and by anything that must not overlap one, such as
    deleting streams whose paths a check found gone.
    """
    return cache_lock(POLL_LOCK_KEY, settings.MEDIAMTX_RECONCILE_LOCK_SECONDS)
