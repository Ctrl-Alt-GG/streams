"""
The check of the media server: which paths the catalog has, what they carry, and which are live.
It records what it found in the database first, and publishes it to the cache only after that
succeeded, so that a failed check leaves the last complete one in place.
"""

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Literal

import httpx
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import transaction
from pydantic import ValidationError

from catalog.models import BlockedPath, Stream
from catalog.services.cache import reconcile_lock
from catalog.services.media import media_kind_from_tracks
from catalog.services.mediamtx import (
    ActivePath,
    ConfiguredPath,
    MediaMTXClient,
    MediaMTXSchemaError,
)
from catalog.services.observations import (
    FailureReason,
    Observation,
    PathObservation,
    publish_observation,
    record_attempt,
    record_failure,
    record_success,
    utc_now,
)
from catalog.services.playback import is_safe_path

logger = logging.getLogger(__name__)

ReconcileOutcome = Literal["refreshed", "locked", "failed"]

# Paths of MediaMTX's configuration that only hold settings for other paths.
_SETTINGS_PATHS = {"all", "all_others"}


def failure_reason(error: Exception) -> FailureReason:
    if isinstance(error, httpx.TimeoutException | SoftTimeLimitExceeded):
        return "timeout"
    if isinstance(error, httpx.HTTPStatusError):
        return "auth" if error.response.status_code in {401, 403} else "server"
    if isinstance(error, httpx.TransportError):
        return "transport"
    if isinstance(error, MediaMTXSchemaError | ValidationError):
        return "schema"
    return "unknown"


def observe(
    active_paths: list[ActivePath], configured_paths: list[ConfiguredPath], observed_at: datetime
) -> Observation:
    """The catalog's paths in the media server's answers: what is live, and what is configured."""
    active_by_name = {path.name: path for path in active_paths}
    names = set(active_by_name)
    names.update(
        path.name
        for path in configured_paths
        if not path.name.startswith("~") and path.name not in _SETTINGS_PATHS
    )
    names.difference_update(BlockedPath.objects.values_list("path_name", flat=True))
    unsafe_names = {name for name in names if not is_safe_path(name)}
    if unsafe_names:
        logger.warning("mediamtx_unsafe_paths_skipped count=%d", len(unsafe_names))
    names.difference_update(unsafe_names)

    paths = {}
    for name in sorted(names):
        path = active_by_name.get(name)
        paths[name] = (
            PathObservation(
                available=path.available,
                available_time=path.available_time,
                online=path.online,
                online_time=path.online_time,
                tracks=tuple(path.tracks2),
                conf_name=path.conf_name,
            )
            if path
            else PathObservation(available=False, online=False, conf_name=name)
        )
    return Observation(observed_at=observed_at, paths=paths)


def record_observation(observation: Observation) -> None:
    """
    The database's part of a check: a stream for every new path, and the observed fields of the
    others, which are the media kind and when the path was last live. It writes nothing else, so
    the organizers' fields keep what they saved meanwhile.
    """
    live_names = [name for name, path in observation.paths.items() if path.available]
    with transaction.atomic():
        Stream.objects.bulk_create(
            [Stream(path_name=name) for name in observation.paths], ignore_conflicts=True
        )
        changed_kinds = []
        for stream in Stream.objects.filter(path_name__in=observation.paths).only(
            "id", "path_name", "media_kind"
        ):
            media_kind = media_kind_from_tracks(observation.paths[stream.path_name].tracks)
            if media_kind != Stream.MediaKind.UNKNOWN and stream.media_kind != media_kind:
                stream.media_kind = media_kind
                changed_kinds.append(stream)
        Stream.objects.bulk_update(changed_kinds, ["media_kind"])
        Stream.objects.filter(path_name__in=live_names).update(last_seen_at=observation.observed_at)


def reconcile_media_server(
    *,
    client_factory: Callable[[], MediaMTXClient] = MediaMTXClient,
    now: Callable[[], datetime] = utc_now,
    clock: Callable[[], float] = time.monotonic,
) -> ReconcileOutcome:
    """
    Checks the media server, unless another check is under way. The whole check has
    MEDIAMTX_RECONCILE_BUDGET_SECONDS, however many pages the media server answers with.
    """
    with reconcile_lock() as acquired:
        if not acquired:
            return "locked"
        started_at = now()
        record_attempt(started_at)
        try:
            deadline = clock() + settings.MEDIAMTX_RECONCILE_BUDGET_SECONDS
            with client_factory() as client:
                active_paths = client.list_active_paths(deadline=deadline)
                configured_paths = client.list_configured_paths(deadline=deadline)
            observation = observe(active_paths, configured_paths, now())
            record_observation(observation)
            publish_observation(observation)
            record_success(observation.observed_at)
        except Exception as error:  # Celery must wait for the next scheduled check.
            reason = failure_reason(error)
            record_failure(reason, now())
            logger.warning(
                "mediamtx_reconcile_failed reason=%s exception=%s message=%s",
                reason,
                type(error).__name__,
                error,
                exc_info=reason == "unknown",
            )
            return "failed"
        logger.info(
            "mediamtx_reconcile_success path_count=%d duration_seconds=%.3f",
            len(observation.paths),
            (now() - started_at).total_seconds(),
        )
        return "refreshed"
