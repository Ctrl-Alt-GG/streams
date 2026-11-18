from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Literal
from urllib.parse import urljoin

from django.conf import settings
from django.db.models import Q, QuerySet
from django.urls import reverse

from catalog.models import BlockedPath, Stream
from catalog.services.media import effective_media_kind
from catalog.services.observations import (
    MediaTrack,
    Observation,
    ObservationState,
    SourceStatus,
    observation_state,
    utc_now,
)
from catalog.services.playback import build_hls_url, is_safe_path
from catalog.services.thumbnails import (
    audio_thumbnail_url,
    fallback_thumbnail_url,
    offline_thumbnail_url,
)

_STREAM_EMOJIS = (
    "🎮",
    "🕹️",
    "👾",
    "🚀",
    "🌟",
    "🔥",
    "🎧",
    "🎤",
    "🎬",
    "🏆",
    "⚡",
    "🌈",
    "💎",
    "🧩",
    "🎲",
    "🪄",
)


StreamStatus = Literal["live", "offline", "unknown"]


@dataclass(frozen=True, slots=True)
class StreamProjection:
    id: str
    path_name: str
    display_name: str
    description: str
    effective_name: str
    status: StreamStatus
    available: bool | None
    online: bool | None
    audio_only: bool
    tracks: tuple[MediaTrack, ...]
    observed_at: str | None
    stale: bool
    watch_url: str
    thumbnail_url: str
    hls_url: str | None

    @property
    def emoji(self) -> str:
        digest = sha256(self.path_name.encode()).digest()
        index = int.from_bytes(digest[:8], byteorder="big") % len(_STREAM_EMOJIS)
        return _STREAM_EMOJIS[index]


@dataclass(frozen=True, slots=True)
class SourceProjection:
    status: SourceStatus
    observed_at: str | None
    age_seconds: float | None
    failure_count: int


@dataclass(frozen=True, slots=True)
class CatalogProjection:
    source: SourceProjection
    streams: tuple[StreamProjection, ...]


# What a projection reads of a stream.
_PROJECTED_FIELDS = ("id", "path_name", "display_name", "description", "media_kind")


def stream_status(path_name: str, observation: Observation | None) -> StreamStatus:
    if observation is None:
        return "unknown"
    return "live" if observation.is_live(path_name) else "offline"


class CatalogService:
    """
    The catalog as pages and the API show it: the streams with what the last check of the media
    server observed. `read_state` and `now` are where the observations and the time come from.
    """

    def __init__(
        self,
        *,
        read_state: Callable[[], ObservationState] = observation_state,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._read_state = read_state
        self._now = now

    def list(self, *, public_urls: bool = True, include_unseen: bool = False) -> CatalogProjection:
        """
        The channels of the catalog. A channel that hasn't been live for longer than
        STREAM_HIDE_OFFLINE_AFTER_DAYS is left out, unless `include_unseen`; its own page stays.
        """
        state = self._read_state()
        records = self._visible_records()
        if not include_unseen:
            records = self._recently_seen(records, state.observation)
        streams = [
            self._project(record, state, public_urls=public_urls)
            for record in records.only(*_PROJECTED_FIELDS)
            if is_safe_path(record.path_name)
        ]
        return CatalogProjection(
            source=self._source(state), streams=tuple(sorted(streams, key=self._sort_key))
        )

    def statuses(self, records: Iterable[Stream]) -> dict[Any, StreamStatus]:
        """Whether each of `records` is live, by primary key, without projecting them."""
        observation = self._read_state().observation
        return {record.pk: stream_status(record.path_name, observation) for record in records}

    def get(
        self, stream_id, *, public_urls: bool = True
    ) -> tuple[SourceProjection, StreamProjection]:
        record = self._visible_records().get(pk=stream_id)
        if not is_safe_path(record.path_name):
            raise Stream.DoesNotExist
        state = self._read_state()
        return self._source(state), self._project(record, state, public_urls=public_urls)

    @staticmethod
    def _visible_records() -> QuerySet[Stream]:
        blocked_paths = BlockedPath.objects.values_list("path_name", flat=True)
        return Stream.objects.exclude(path_name__in=blocked_paths)

    def _recently_seen(
        self, records: QuerySet[Stream], observation: Observation | None
    ) -> QuerySet[Stream]:
        # Also while the status is unknown, so that an outage of the status source doesn't bring
        # old channels back. A channel that was live a moment ago was seen at the last check.
        days = settings.STREAM_HIDE_OFFLINE_AFTER_DAYS
        if not days:
            return records
        cutoff = self._now() - timedelta(days=days)
        live_paths = (
            [name for name, path in observation.paths.items() if path.available]
            if observation is not None
            else []
        )
        return records.filter(
            Q(last_seen_at__gte=cutoff)
            | Q(last_seen_at__isnull=True, created_at__gte=cutoff)
            | Q(path_name__in=live_paths)
        )

    @staticmethod
    def _source(state: ObservationState) -> SourceProjection:
        return SourceProjection(
            status=state.status,
            observed_at=(
                state.observation.observed_at.isoformat() if state.observation is not None else None
            ),
            age_seconds=state.age_seconds,
            failure_count=state.failure_count,
        )

    def _project(
        self, record: Stream, state: ObservationState, *, public_urls: bool
    ) -> StreamProjection:
        observation = state.observation
        path = observation.path(record.path_name) if observation is not None else None
        status = stream_status(record.path_name, observation)
        audio_only = effective_media_kind(record.media_kind, path) == "audio"
        watch_path = reverse("catalog:stream-detail", kwargs={"stream_id": record.id})
        watch_url = self._catalog_url(watch_path, public=public_urls)
        if audio_only:
            thumbnail_url = audio_thumbnail_url(record.path_name, public=public_urls)
        elif status == "live":
            thumbnail_path = reverse("catalog:stream-thumbnail", kwargs={"stream_id": record.id})
            thumbnail_url = self._catalog_url(thumbnail_path, public=public_urls)
        elif status == "offline":
            thumbnail_url = offline_thumbnail_url(public=public_urls)
        else:
            thumbnail_url = fallback_thumbnail_url(record.path_name, public=public_urls)
        return StreamProjection(
            id=str(record.id),
            path_name=record.path_name,
            display_name=record.display_name,
            description=record.description,
            effective_name=record.effective_name,
            status=status,
            available=None if observation is None else status == "live",
            online=None if observation is None else (path.online if path is not None else False),
            audio_only=audio_only,
            tracks=path.tracks if path is not None else (),
            observed_at=self._source(state).observed_at,
            stale=state.status != "fresh",
            watch_url=watch_url,
            thumbnail_url=thumbnail_url,
            hls_url=build_hls_url(record.path_name) if status == "live" else None,
        )

    @staticmethod
    def _catalog_url(path: str, *, public: bool) -> str:
        if not public:
            return path
        return urljoin(f"{settings.PUBLIC_BASE_URL.rstrip('/')}/", path.lstrip("/"))

    @staticmethod
    def _sort_key(stream: StreamProjection) -> tuple:
        rank = {"live": 0, "offline": 1, "unknown": 2}
        return rank[stream.status], stream.effective_name.casefold(), stream.id
