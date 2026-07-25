from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_KEY = f"catalog:mediamtx:snapshot:v{SNAPSHOT_SCHEMA_VERSION}"
POLL_HEALTH_KEY = "catalog:mediamtx:poll-health:v1"
POLL_LOCK_KEY = "catalog:mediamtx:poll-lock:v1"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def get_snapshot() -> dict | None:
    snapshot = cache.get(SNAPSHOT_KEY)
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
        or not isinstance(snapshot.get("observed_at"), str)
        or not isinstance(snapshot.get("paths"), dict)
    ):
        return None
    return snapshot


def write_snapshot(snapshot: dict) -> None:
    payload = {**snapshot, "schema_version": SNAPSHOT_SCHEMA_VERSION}
    cache.set(SNAPSHOT_KEY, payload, timeout=settings.MEDIAMTX_SNAPSHOT_RETENTION_SECONDS)


def get_poll_health() -> dict | None:
    health = cache.get(POLL_HEALTH_KEY)
    return health if isinstance(health, dict) else None


def record_attempt() -> None:
    previous = get_poll_health() or {}
    health = {
        "last_attempt_at": _now(),
        "last_success_at": previous.get("last_success_at"),
        "last_error_at": previous.get("last_error_at"),
        "consecutive_failures": previous.get("consecutive_failures", 0),
        "reason": previous.get("reason"),
    }
    cache.set(POLL_HEALTH_KEY, health, timeout=settings.MEDIAMTX_SNAPSHOT_RETENTION_SECONDS)


def record_success(observed_at: str) -> None:
    health = {
        "last_attempt_at": observed_at,
        "last_success_at": observed_at,
        "last_error_at": None,
        "consecutive_failures": 0,
        "reason": None,
    }
    cache.set(POLL_HEALTH_KEY, health, timeout=settings.MEDIAMTX_SNAPSHOT_RETENTION_SECONDS)


def record_failure(reason: str) -> None:
    previous = get_poll_health() or {}
    now = _now()
    health = {
        "last_attempt_at": now,
        "last_success_at": previous.get("last_success_at"),
        "last_error_at": now,
        "consecutive_failures": previous.get("consecutive_failures", 0) + 1,
        "reason": reason,
    }
    cache.set(POLL_HEALTH_KEY, health, timeout=settings.MEDIAMTX_SNAPSHOT_RETENTION_SECONDS)


@contextmanager
def reconcile_lock() -> Iterator[bool]:
    timeout = settings.MEDIAMTX_RECONCILE_LOCK_SECONDS
    lock_factory = getattr(cache, "lock", None)
    if lock_factory is not None:
        lock = lock_factory(POLL_LOCK_KEY, timeout=timeout, blocking_timeout=0)
        acquired = lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()
        return

    token = uuid4().hex
    acquired = cache.add(POLL_LOCK_KEY, token, timeout=timeout)
    try:
        yield acquired
    finally:
        if acquired and cache.get(POLL_LOCK_KEY) == token:
            cache.delete(POLL_LOCK_KEY)
