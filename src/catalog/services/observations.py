"""
What the catalog knows of the media server: the paths of its last complete check, with their
tracks, and how that check and the ones after it went. The status worker writes both after each
check; the web processes and the capture worker read them from the cache.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from django.conf import settings
from django.core.cache import cache
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    OnErrorOmit,
    ValidationError,
    WrapValidator,
    model_validator,
)

from catalog.services.cache import read_public_cache

logger = logging.getLogger(__name__)

OBSERVATION_SCHEMA_VERSION = 2
OBSERVATION_KEY = f"catalog:mediamtx:snapshot:v{OBSERVATION_SCHEMA_VERSION}"
HEALTH_KEY = "catalog:mediamtx:poll-health:v2"

SourceStatus = Literal["fresh", "stale", "unavailable"]
FailureReason = Literal["auth", "schema", "server", "timeout", "transport", "unknown"]


def utc_now() -> datetime:
    return datetime.now(UTC)


def _unknown_if_invalid(value: Any, handler) -> Any:
    # A property in a form the catalog can't use counts as unknown. One odd value of one channel
    # mustn't drop its track, or the whole check with every other channel.
    try:
        return handler(value)
    except ValidationError:
        return None


Dimension = Annotated[
    int | None, Field(strict=True, ge=1, le=65_535), WrapValidator(_unknown_if_invalid)
]
SampleRate = Annotated[
    int | None, Field(strict=True, ge=1, le=1_000_000), WrapValidator(_unknown_if_invalid)
]
ChannelCount = Annotated[
    int | None, Field(strict=True, ge=1, le=255), WrapValidator(_unknown_if_invalid)
]


class MediaTrack(BaseModel):
    """
    A track of a path. It reads both what MediaMTX reports in `tracks2`, whose properties are in
    `codecProps`, and the catalog's own record of it.
    """

    model_config = ConfigDict(frozen=True)

    codec: str = "unknown"
    width: Dimension = None
    height: Dimension = None
    sample_rate: SampleRate = None
    channel_count: ChannelCount = None

    @model_validator(mode="before")
    @classmethod
    def _from_media_server(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        properties = data.get("codecProps")
        if not isinstance(properties, Mapping):
            properties = data
        return {
            "codec": str(data.get("codec") or data.get("type") or "unknown"),
            "width": properties.get("width"),
            "height": properties.get("height"),
            "sample_rate": properties.get("sample_rate", properties.get("sampleRate")),
            "channel_count": properties.get("channel_count", properties.get("channelCount")),
        }


# A track that isn't an object at all is left out; the path keeps its other tracks.
MediaTracks = list[OnErrorOmit[MediaTrack]]


class PathObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    available: bool
    available_time: str | None = None
    online: bool | None = None
    online_time: str | None = None
    tracks: tuple[MediaTrack, ...] = ()
    conf_name: str | None = None


class Observation(BaseModel):
    """One complete check of the media server: every path of the catalog, as it was then."""

    model_config = ConfigDict(frozen=True)

    observed_at: AwareDatetime
    paths: dict[str, PathObservation]

    def path(self, name: str) -> PathObservation | None:
        return self.paths.get(name)

    def is_live(self, name: str) -> bool:
        path = self.paths.get(name)
        return path is not None and path.available


class CheckHealth(BaseModel):
    """How the checks of the media server went: the last success, and the failures since."""

    last_attempt_at: AwareDatetime | None = None
    last_success_at: AwareDatetime | None = None
    last_error_at: AwareDatetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    reason: FailureReason | None = None


@dataclass(frozen=True, slots=True)
class ObservationState:
    """
    The last complete check, and whether it still tells how things are. It is `stale` once it is
    old, or a later check failed; `unavailable` without one, as while the cache is down.
    """

    status: SourceStatus
    observation: Observation | None
    age_seconds: float | None
    failure_count: int

    @property
    def fresh_observation(self) -> Observation | None:
        return self.observation if self.status == "fresh" else None


def read_observation() -> Observation | None:
    payload = read_public_cache(OBSERVATION_KEY)
    if not isinstance(payload, dict):
        return None
    try:
        return Observation.model_validate(payload)
    except ValidationError:
        logger.warning("catalog_observation_unreadable key=%s", OBSERVATION_KEY)
        return None


def publish_observation(observation: Observation) -> None:
    cache.set(
        OBSERVATION_KEY,
        observation.model_dump(mode="json"),
        timeout=settings.MEDIAMTX_SNAPSHOT_RETENTION_SECONDS,
    )


def read_health() -> CheckHealth:
    payload = read_public_cache(HEALTH_KEY)
    try:
        return CheckHealth.model_validate(payload) if isinstance(payload, dict) else CheckHealth()
    except ValidationError:
        return CheckHealth()


def _write_health(health: CheckHealth) -> None:
    cache.set(
        HEALTH_KEY,
        health.model_dump(mode="json"),
        timeout=settings.MEDIAMTX_SNAPSHOT_RETENTION_SECONDS,
    )


def record_attempt(at: datetime) -> None:
    _write_health(read_health().model_copy(update={"last_attempt_at": at}))


def record_success(at: datetime) -> None:
    _write_health(CheckHealth(last_attempt_at=at, last_success_at=at))


def record_failure(reason: FailureReason, at: datetime) -> None:
    health = read_health()
    _write_health(
        health.model_copy(
            update={
                "last_attempt_at": at,
                "last_error_at": at,
                "consecutive_failures": health.consecutive_failures + 1,
                "reason": reason,
            }
        )
    )


def observation_state(now: datetime | None = None) -> ObservationState:
    observation = read_observation()
    health = read_health()
    if observation is None:
        return ObservationState("unavailable", None, None, health.consecutive_failures)
    age = max(0.0, ((now or utc_now()) - observation.observed_at).total_seconds())
    failed_since = health.last_error_at is not None and (
        health.last_success_at is None or health.last_error_at > health.last_success_at
    )
    status = "stale" if age >= settings.MEDIAMTX_FRESHNESS_SECONDS or failed_since else "fresh"
    return ObservationState(status, observation, round(age, 3), health.consecutive_failures)
