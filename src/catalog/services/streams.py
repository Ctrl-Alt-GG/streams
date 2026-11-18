"""Deleting streams, which mustn't overlap a check of the media server."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from django.db import transaction
from django.db.models import QuerySet

from catalog.models import BlockedPath, Stream
from catalog.services.cache import reconcile_lock
from catalog.services.observations import Observation, observation_state

# Why nothing was deleted: no recent, successful check confirms which paths are gone, or a check
# is under way, which might find a path back.
DeletionRefusal = Literal["unconfirmed", "busy"]


@dataclass(frozen=True, slots=True)
class DeletionGroups:
    """
    Streams sorted for deletion: those whose paths the media server no longer has (`absent`), and
    those that are kept, as the media server still has them (`published`) or as they are hidden,
    which the check leaves out (`hidden`).
    """

    absent: tuple[Stream, ...]
    published: tuple[Stream, ...]
    hidden: tuple[Stream, ...]


def sort_for_deletion(
    streams: Iterable[Stream], observation: Observation, hidden_paths: set[str]
) -> DeletionGroups:
    groups: dict[str, list[Stream]] = {"absent": [], "published": [], "hidden": []}
    for stream in streams:
        if stream.path_name in hidden_paths:
            groups["hidden"].append(stream)
        elif observation.path(stream.path_name) is not None:
            groups["published"].append(stream)
        else:
            groups["absent"].append(stream)
    return DeletionGroups(**{name: tuple(group) for name, group in groups.items()})


def delete_absent_streams(
    stream_ids: Iterable[Any],
    *,
    before_delete: Callable[[QuerySet[Stream]], object] | None = None,
) -> DeletionGroups | DeletionRefusal:
    """
    Deletes those of the streams whose paths a recent, successful check found gone. Any other
    stream might be back, and the next check would create it again under a new address, without
    its name and description.

    It holds the lock of the checks, so that no check that might find a path back is under way,
    and sorts the streams as they are in the database then. `before_delete` gets the streams that
    are about to be deleted.
    """
    with reconcile_lock() as acquired:
        if not acquired:
            return "busy"
        observation = observation_state().fresh_observation
        if observation is None:
            return "unconfirmed"
        with transaction.atomic():
            streams = list(Stream.objects.select_for_update().filter(pk__in=list(stream_ids)))
            hidden_paths = set(
                BlockedPath.objects.filter(
                    path_name__in=[stream.path_name for stream in streams]
                ).values_list("path_name", flat=True)
            )
            groups = sort_for_deletion(streams, observation, hidden_paths)
            if groups.absent:
                deleted = Stream.objects.filter(pk__in=[stream.pk for stream in groups.absent])
                if before_delete is not None:
                    before_delete(deleted)
                deleted.delete()
        return groups
