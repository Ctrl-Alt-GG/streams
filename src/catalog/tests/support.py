"""What the tests share: a media server to check, and observations to publish."""

from datetime import UTC, datetime
from typing import Any

from django.contrib.auth import get_user_model

from catalog.services.mediamtx import ActivePath, ConfiguredPath
from catalog.services.observations import Observation, publish_observation
from catalog.services.reconciliation import reconcile_media_server


class FakeMediaServer:
    """A media server with `active` paths and `configured` paths, for `reconcile_media_server`."""

    def __init__(self, active=(), configured=(), error: Exception | None = None):
        self.active = list(active)
        self.configured = list(configured)
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def list_active_paths(self, *, deadline=None) -> list[ActivePath]:
        return self.active

    def list_configured_paths(self, *, deadline=None) -> list[ConfiguredPath]:
        if self.error is not None:
            raise self.error
        return self.configured


def live_video(name: str, **properties: Any) -> ActivePath:
    return ActivePath(
        name=name, available=True, tracks2=[{"codec": "H264", "codecProps": properties}]
    )


class MediaServerMixin:
    def reconcile(self, names, server: FakeMediaServer | None = None) -> str:
        """Runs a check that finds `names` live on the media server, with a video track each."""
        server = server or FakeMediaServer([live_video(name) for name in names])
        outcome = reconcile_media_server(client_factory=lambda: server)
        self.assertEqual(outcome, "refreshed")
        return outcome

    def organizer(self):
        return get_user_model().objects.create_superuser("owner", "owner@example.org", "password")


def observe(paths: dict[str, dict], at: datetime | None = None) -> Observation:
    """Publishes a check that found `paths`, as the status worker does."""
    observation = Observation.model_validate(
        {"observed_at": at or datetime.now(UTC), "paths": paths}
    )
    publish_observation(observation)
    return observation
