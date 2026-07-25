import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.db import transaction
from pydantic import ValidationError

from catalog.models import BlockedPath, Stream
from catalog.services.cache import (
    reconcile_lock,
    record_attempt,
    record_failure,
    record_success,
    write_snapshot,
)
from catalog.services.mediamtx import ActivePath, MediaMTXClient, MediaMTXSchemaError

logger = logging.getLogger(__name__)


def _track(track: dict[str, Any]) -> dict:
    codec = str(track.get("codec") or track.get("type") or "unknown")
    return {
        "codec": codec,
        "width": track.get("width"),
        "height": track.get("height"),
        "sample_rate": track.get("sampleRate"),
        "channel_count": track.get("channelCount"),
    }


def _path_payload(path: ActivePath) -> dict:
    return {
        "available": path.available,
        "available_time": path.available_time,
        "online": path.online,
        "online_time": path.online_time,
        "tracks": [_track(track) for track in path.tracks2],
        "conf_name": path.conf_name,
    }


def _reason(error: Exception) -> str:
    if isinstance(error, httpx.TimeoutException | SoftTimeLimitExceeded):
        return "timeout"
    if isinstance(error, httpx.HTTPStatusError):
        if error.response.status_code in {401, 403}:
            return "auth"
        return "server"
    if isinstance(error, httpx.TransportError):
        return "transport"
    if isinstance(error, MediaMTXSchemaError | ValidationError):
        return "schema"
    return "unknown"


@shared_task(
    bind=True,
    name="catalog.tasks.refresh_mediamtx_snapshot",
    ignore_result=True,
    acks_late=True,
)
def refresh_mediamtx_snapshot(self) -> str:
    with reconcile_lock() as acquired:
        if not acquired:
            return "locked"
        record_attempt()
        started_at = datetime.now(UTC)
        try:
            with MediaMTXClient() as client:
                active_paths = client.list_active_paths()
                configured_paths = client.list_configured_paths()

            active_by_name = {path.name: path for path in active_paths}
            names = set(active_by_name)
            names.update(path.name for path in configured_paths if not path.name.startswith("~"))
            names.difference_update(BlockedPath.objects.values_list("path_name", flat=True))
            with transaction.atomic():
                Stream.objects.bulk_create(
                    [Stream(path_name=name) for name in sorted(names)], ignore_conflicts=True
                )
                Stream.objects.exclude(path_name__in=names).filter(
                    display_name="",
                    description="",
                ).delete()

            observed_at = datetime.now(UTC).isoformat()
            paths = {}
            for name in sorted(names):
                path = active_by_name.get(name)
                paths[name] = (
                    _path_payload(path)
                    if path
                    else {
                        "available": False,
                        "available_time": None,
                        "online": False,
                        "online_time": None,
                        "tracks": [],
                        "conf_name": name,
                    }
                )
            write_snapshot({"observed_at": observed_at, "paths": paths})
            record_success(observed_at)
            duration = (datetime.now(UTC) - started_at).total_seconds()
            logger.info(
                "mediamtx_reconcile_success path_count=%d duration_seconds=%.3f",
                len(paths),
                duration,
            )
            return "refreshed"
        except Exception as error:  # Celery must wait for the next scheduled reconciliation.
            reason = _reason(error)
            record_failure(reason)
            logger.warning(
                "mediamtx_reconcile_failed reason=%s exception=%s",
                reason,
                type(error).__name__,
            )
            return "failed"
