import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import transaction
from pydantic import ValidationError

from catalog.models import BlockedPath, Stream
from catalog.services.cache import (
    get_snapshot,
    reconcile_lock,
    record_attempt,
    record_failure,
    record_success,
    write_snapshot,
)
from catalog.services.media import media_kind_from_tracks
from catalog.services.mediamtx import ActivePath, MediaMTXClient, MediaMTXSchemaError
from catalog.services.playback import build_hls_capture_url
from catalog.services.thumbnails import (
    ThumbnailKind,
    capture_thumbnail,
    delete_thumbnail,
    get_thumbnail,
    thumbnail_lock,
    thumbnail_needs_refresh,
    write_thumbnail,
)

logger = logging.getLogger(__name__)


def _track(track: dict[str, Any]) -> dict:
    codec = str(track.get("codec") or track.get("type") or "unknown")
    codec_props = track.get("codecProps")
    properties = codec_props if isinstance(codec_props, dict) else track
    return {
        "codec": codec,
        "width": properties.get("width"),
        "height": properties.get("height"),
        "sample_rate": properties.get("sampleRate"),
        "channel_count": properties.get("channelCount"),
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


def _desired_thumbnail_kind(
    snapshot: dict | None,
    path_name: str,
    media_kind: str = Stream.MediaKind.UNKNOWN,
) -> ThumbnailKind:
    if snapshot is None:
        return "audio" if media_kind == Stream.MediaKind.AUDIO else "fallback"
    path = snapshot.get("paths", {}).get(path_name)
    if not isinstance(path, dict):
        return "audio" if media_kind == Stream.MediaKind.AUDIO else "fallback"
    observed_media_kind = media_kind_from_tracks(path.get("tracks"))
    effective_media_kind = (
        media_kind if observed_media_kind == Stream.MediaKind.UNKNOWN else observed_media_kind
    )
    if effective_media_kind == Stream.MediaKind.AUDIO:
        return "audio"
    if not path.get("available"):
        return "offline"
    return "frame"


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
            names.update(
                path.name
                for path in configured_paths
                if not path.name.startswith("~") and path.name not in {"all", "all_others"}
            )
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
            streams_by_path = Stream.objects.filter(path_name__in=paths).in_bulk(
                field_name="path_name"
            )
            media_kind_updates = []
            for name, path_payload in paths.items():
                media_kind = media_kind_from_tracks(path_payload["tracks"])
                stream = streams_by_path[name]
                if media_kind != Stream.MediaKind.UNKNOWN and stream.media_kind != media_kind:
                    stream.media_kind = media_kind
                    media_kind_updates.append(stream)
            if media_kind_updates:
                Stream.objects.bulk_update(media_kind_updates, ["media_kind"])
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


@shared_task(
    name="catalog.tasks.refresh_stream_thumbnail",
    ignore_result=True,
    acks_late=True,
)
def refresh_stream_thumbnail(stream_id: str) -> str:
    try:
        stream = Stream.objects.only("id", "path_name", "media_kind").get(pk=stream_id)
    except Stream.DoesNotExist:
        return "missing"

    snapshot = get_snapshot()
    if snapshot is None:
        return "retained" if get_thumbnail(stream.id) is not None else "fallback"
    desired_kind = _desired_thumbnail_kind(snapshot, stream.path_name, stream.media_kind)
    if desired_kind != "frame":
        delete_thumbnail(stream.id)
        return desired_kind
    if not thumbnail_needs_refresh(stream.id):
        return "fresh"

    with thumbnail_lock(stream.id) as acquired:
        if not acquired:
            return "locked"
        snapshot = get_snapshot()
        if snapshot is None:
            return "retained" if get_thumbnail(stream.id) is not None else "fallback"
        desired_kind = _desired_thumbnail_kind(snapshot, stream.path_name, stream.media_kind)
        if desired_kind != "frame":
            delete_thumbnail(stream.id)
            return desired_kind
        if not thumbnail_needs_refresh(stream.id):
            return "fresh"

        try:
            content = capture_thumbnail(build_hls_capture_url(stream.path_name))
        except SoftTimeLimitExceeded:
            raise
        except Exception as error:
            logger.warning(
                "stream_thumbnail_capture_failed stream_id=%s exception=%s",
                stream.id,
                type(error).__name__,
            )
        else:
            snapshot = get_snapshot()
            desired_kind = _desired_thumbnail_kind(snapshot, stream.path_name, stream.media_kind)
            if snapshot is not None and desired_kind != "frame":
                delete_thumbnail(stream.id)
                return desired_kind
            write_thumbnail(stream.id, content)
            return "captured"

        thumbnail = get_thumbnail(stream.id)
        return "retained" if thumbnail is not None else "fallback"


@shared_task(
    name="catalog.tasks.refresh_stream_thumbnails",
    ignore_result=True,
    acks_late=True,
)
def refresh_stream_thumbnails() -> str:
    snapshot = get_snapshot()
    if snapshot is None:
        return "queued:0"
    queued = 0
    for stream in Stream.objects.only("id", "path_name", "media_kind").iterator():
        desired_kind = _desired_thumbnail_kind(snapshot, stream.path_name, stream.media_kind)
        if desired_kind != "frame":
            delete_thumbnail(stream.id)
        elif thumbnail_needs_refresh(stream.id):
            refresh_stream_thumbnail.apply_async(
                args=(str(stream.id),),
                expires=settings.STREAM_THUMBNAIL_TASK_EXPIRES_SECONDS,
            )
            queued += 1
    return f"queued:{queued}"
