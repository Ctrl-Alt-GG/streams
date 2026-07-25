from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin

from django.conf import settings
from django.urls import reverse

from catalog.models import BlockedPath, Stream
from catalog.services.cache import get_poll_health, get_snapshot
from catalog.services.playback import build_hls_embed_url, build_hls_url


@dataclass(frozen=True, slots=True)
class Track:
    codec: str
    width: int | None = None
    height: int | None = None
    sample_rate: int | None = None
    channel_count: int | None = None


@dataclass(frozen=True, slots=True)
class StreamProjection:
    id: str
    path_name: str
    display_name: str
    description: str
    effective_name: str
    status: str
    available: bool | None
    online: bool | None
    tracks: tuple[Track, ...]
    observed_at: str | None
    stale: bool
    watch_url: str
    hls_url: str | None
    hls_embed_url: str | None

    def as_public_dict(self) -> dict:
        data = asdict(self)
        data.pop("path_name")
        data.pop("hls_embed_url")
        data["tracks"] = [asdict(track) for track in self.tracks]
        return data


@dataclass(frozen=True, slots=True)
class SourceProjection:
    status: str
    observed_at: str | None
    age_seconds: float | None
    failure_count: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CatalogProjection:
    source: SourceProjection
    streams: tuple[StreamProjection, ...]


class CatalogService:
    def list(self) -> CatalogProjection:
        snapshot = get_snapshot()
        health = get_poll_health() or {}
        source, usable_snapshot = self._source(snapshot, health)
        blocked_paths = BlockedPath.objects.values_list("path_name", flat=True)
        discovered_paths = (snapshot or {}).get("paths", {})
        records = tuple(
            Stream.objects.filter(path_name__in=discovered_paths).exclude(
                path_name__in=blocked_paths
            )
        )
        streams = tuple(self._project(record, usable_snapshot, source) for record in records)
        return CatalogProjection(source=source, streams=tuple(sorted(streams, key=self._sort_key)))

    def get(self, stream_id) -> tuple[SourceProjection, StreamProjection]:
        catalog = self.list()
        for stream in catalog.streams:
            if stream.id == str(stream_id):
                return catalog.source, stream
        raise Stream.DoesNotExist

    def _source(self, snapshot: dict | None, health: dict) -> tuple[SourceProjection, dict | None]:
        if not snapshot:
            return (
                SourceProjection(
                    status="unavailable",
                    observed_at=None,
                    age_seconds=None,
                    failure_count=health.get("consecutive_failures", 0),
                ),
                None,
            )

        observed_at = snapshot.get("observed_at")
        try:
            observed = datetime.fromisoformat(observed_at)
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=UTC)
            age = max(0.0, (datetime.now(UTC) - observed).total_seconds())
        except (TypeError, ValueError):
            return SourceProjection("unavailable", None, None, 0), None

        if age >= settings.MEDIAMTX_CACHE_TTL_SECONDS:
            return SourceProjection("unavailable", observed_at, age, 0), None
        last_error = health.get("last_error_at")
        last_success = health.get("last_success_at")
        source_status = (
            "stale" if last_error and (not last_success or last_error > last_success) else "fresh"
        )
        return (
            SourceProjection(
                status=source_status,
                observed_at=observed_at,
                age_seconds=round(age, 3),
                failure_count=health.get("consecutive_failures", 0),
            ),
            snapshot,
        )

    def _project(
        self, record: Stream, snapshot: dict | None, source: SourceProjection
    ) -> StreamProjection:
        path = snapshot.get("paths", {}).get(record.path_name) if snapshot else None
        if snapshot is None:
            status = "unknown"
            available = None
            online = None
            tracks = ()
        else:
            available = bool(path and path.get("available"))
            online = path.get("online") if path else False
            status = "live" if available else "offline"
            tracks = tuple(Track(**track) for track in (path or {}).get("tracks", []))
        watch_path = reverse("catalog:stream-detail", kwargs={"stream_id": record.id})
        watch_url = urljoin(f"{settings.PUBLIC_BASE_URL.rstrip('/')}/", watch_path.lstrip("/"))
        return StreamProjection(
            id=str(record.id),
            path_name=record.path_name,
            display_name=record.display_name,
            description=record.description,
            effective_name=record.effective_name,
            status=status,
            available=available,
            online=online,
            tracks=tracks,
            observed_at=source.observed_at,
            stale=source.status != "fresh",
            watch_url=watch_url,
            hls_url=build_hls_url(record.path_name) if status == "live" else None,
            hls_embed_url=build_hls_embed_url(record.path_name) if status == "live" else None,
        )

    @staticmethod
    def _sort_key(stream: StreamProjection) -> tuple:
        rank = {"live": 0, "offline": 1, "unknown": 2}
        return rank[stream.status], stream.effective_name.casefold(), stream.id
