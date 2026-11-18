"""
Checks of the settings that the catalog depends on together: the addresses it builds links and
requests from, and the durations whose relations keep the worker's checks, locks and caches
consistent (see the timing settings in `config.settings.base`).
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, register

_ADDRESSES = {
    "PUBLIC_BASE_URL": {"http", "https"},
    "MEDIAMTX_API_BASE_URL": {"http", "https"},
    "MEDIAMTX_HLS_PUBLIC_BASE_URL": {"http", "https"},
    "MEDIAMTX_HLS_CAPTURE_BASE_URL": {"http", "https"},
    "MEDIAMTX_RTMP_PUBLIC_BASE_URL": {"rtmp", "rtmps"},
}
_POSITIVE = (
    "MEDIAMTX_API_CONNECT_TIMEOUT",
    "MEDIAMTX_API_READ_TIMEOUT",
    "MEDIAMTX_RECONCILE_INTERVAL_SECONDS",
    "MEDIAMTX_RECONCILE_BUDGET_SECONDS",
    "MEDIAMTX_FRESHNESS_SECONDS",
    "MEDIAMTX_SNAPSHOT_RETENTION_SECONDS",
    "STREAM_THUMBNAIL_CAPTURE_TIMEOUT_SECONDS",
    "STREAM_THUMBNAIL_REFRESH_SECONDS",
    "STREAM_THUMBNAIL_CACHE_SECONDS",
    "STREAM_THUMBNAIL_CAPTURES_PER_SCAN",
)
# (shorter, longer, why): `shorter` has to be less than `longer`.
_ORDER = (
    (
        "MEDIAMTX_RECONCILE_BUDGET_SECONDS",
        "CELERY_TASK_SOFT_TIME_LIMIT",
        "A check of the media server has to end before Celery stops it.",
    ),
    (
        "STREAM_THUMBNAIL_CAPTURE_TIMEOUT_SECONDS",
        "CELERY_TASK_SOFT_TIME_LIMIT",
        "A capture has to give up before Celery stops it.",
    ),
    (
        "CELERY_TASK_TIME_LIMIT",
        "MEDIAMTX_RECONCILE_LOCK_SECONDS",
        "The lock of a check has to outlast the task, or two checks could overlap.",
    ),
    (
        "CELERY_TASK_TIME_LIMIT",
        "STREAM_THUMBNAIL_LOCK_SECONDS",
        "The lock of a capture has to outlast the task, or two captures could overlap.",
    ),
    (
        "MEDIAMTX_RECONCILE_INTERVAL_SECONDS",
        "MEDIAMTX_FRESHNESS_SECONDS",
        "Every check would count as stale before the next one.",
    ),
    (
        "MEDIAMTX_FRESHNESS_SECONDS",
        "MEDIAMTX_SNAPSHOT_RETENTION_SECONDS",
        "A check would be gone before it counted as stale.",
    ),
)


@register()
def check_streams_settings(app_configs=None, **kwargs) -> list[Error]:
    errors = []
    for name, schemes in _ADDRESSES.items():
        parts = urlsplit(str(getattr(settings, name)))
        if parts.scheme not in schemes or not parts.hostname:
            errors.append(
                Error(
                    f"{name} has to be an absolute address with {' or '.join(sorted(schemes))}.",
                    id="streams.E001",
                )
            )
    for name in _POSITIVE:
        if not getattr(settings, name) > 0:
            errors.append(Error(f"{name} has to be greater than zero.", id="streams.E002"))
    if settings.STREAM_HIDE_OFFLINE_AFTER_DAYS < 0:
        errors.append(
            Error(
                "STREAM_HIDE_OFFLINE_AFTER_DAYS can't be negative. 0 keeps every channel.",
                id="streams.E003",
            )
        )
    for shorter, longer, why in _ORDER:
        if not getattr(settings, shorter) < getattr(settings, longer):
            errors.append(
                Error(f"{shorter} has to be less than {longer}.", hint=why, id="streams.E004")
            )
    if settings.STREAM_THUMBNAIL_CACHE_SECONDS < settings.STREAM_THUMBNAIL_REFRESH_SECONDS:
        errors.append(
            Error(
                "STREAM_THUMBNAIL_CACHE_SECONDS can't be less than "
                "STREAM_THUMBNAIL_REFRESH_SECONDS.",
                hint="Thumbnails would expire before they are refreshed.",
                id="streams.E004",
            )
        )
    return errors
