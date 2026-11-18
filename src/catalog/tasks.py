import logging
from datetime import UTC, datetime

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings

from catalog.models import Stream
from catalog.services.observations import read_observation
from catalog.services.playback import build_hls_capture_url
from catalog.services.reconciliation import reconcile_media_server
from catalog.services.thumbnails import (
    capture_thumbnail,
    delete_thumbnail,
    desired_thumbnail_kind,
    get_thumbnail,
    thumbnail_generated_at,
    thumbnail_lock,
    thumbnail_needs_refresh,
    write_thumbnail,
)

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="catalog.tasks.refresh_mediamtx_snapshot",
    ignore_result=True,
    acks_late=True,
)
def refresh_mediamtx_snapshot(self) -> str:
    return reconcile_media_server()


@shared_task(
    name="catalog.tasks.refresh_stream_thumbnail",
    ignore_result=True,
    acks_late=True,
)
def refresh_stream_thumbnail(stream_id: str, path_name: str, media_kind: str) -> str:
    """
    Captures a frame of a live video stream. This runs on the capture worker, which decodes
    video that any publisher controls and therefore has no database: the stream's details come
    with the task.
    """
    observation = read_observation()
    if observation is None:
        return "retained" if get_thumbnail(stream_id) is not None else "fallback"
    desired_kind = desired_thumbnail_kind(observation, path_name, media_kind)
    if desired_kind != "frame":
        delete_thumbnail(stream_id)
        return desired_kind
    if not thumbnail_needs_refresh(stream_id):
        return "fresh"

    with thumbnail_lock(stream_id) as acquired:
        if not acquired:
            return "locked"
        observation = read_observation()
        if observation is None:
            return "retained" if get_thumbnail(stream_id) is not None else "fallback"
        desired_kind = desired_thumbnail_kind(observation, path_name, media_kind)
        if desired_kind != "frame":
            delete_thumbnail(stream_id)
            return desired_kind
        if not thumbnail_needs_refresh(stream_id):
            return "fresh"

        try:
            content = capture_thumbnail(build_hls_capture_url(path_name))
        except SoftTimeLimitExceeded:
            raise
        except Exception as error:
            logger.warning(
                "stream_thumbnail_capture_failed stream_id=%s exception=%s message=%s",
                stream_id,
                type(error).__name__,
                error,
            )
        else:
            observation = read_observation()
            desired_kind = desired_thumbnail_kind(observation, path_name, media_kind)
            if observation is not None and desired_kind != "frame":
                delete_thumbnail(stream_id)
                return desired_kind
            write_thumbnail(stream_id, content)
            return "captured"

        thumbnail = get_thumbnail(stream_id)
        return "retained" if thumbnail is not None else "fallback"


@shared_task(
    name="catalog.tasks.refresh_stream_thumbnails",
    ignore_result=True,
    acks_late=True,
)
def refresh_stream_thumbnails() -> str:
    """
    Queues captures for the live video streams whose thumbnails are due, the oldest first. Only
    as many as the capture worker manages before the next scan are queued
    (STREAM_THUMBNAIL_CAPTURES_PER_SCAN): queued captures expire after the scan interval, and
    with more of them the same streams would expire every time while the others never got theirs.
    """
    observation = read_observation()
    if observation is None:
        return "queued:0"
    oldest = datetime.min.replace(tzinfo=UTC)
    due = []
    for stream in Stream.objects.only("id", "path_name", "media_kind").iterator():
        if desired_thumbnail_kind(observation, stream.path_name, stream.media_kind) != "frame":
            delete_thumbnail(stream.id)
        elif thumbnail_needs_refresh(stream.id):
            due.append((thumbnail_generated_at(stream.id) or oldest, str(stream.id), stream))
    due.sort(key=lambda entry: entry[:2])
    for _, _, stream in due[: settings.STREAM_THUMBNAIL_CAPTURES_PER_SCAN]:
        refresh_stream_thumbnail.apply_async(
            args=(str(stream.id), stream.path_name, stream.media_kind),
            expires=settings.STREAM_THUMBNAIL_TASK_EXPIRES_SECONDS,
        )
    return f"queued:{min(len(due), settings.STREAM_THUMBNAIL_CAPTURES_PER_SCAN)}"
