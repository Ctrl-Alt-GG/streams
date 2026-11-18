"""
Against PostgreSQL and Redis, with `config.settings.integration`; skipped otherwise. These are the
writes, locks and migrations that SQLite and the in-memory cache can't show.
"""

import threading
import time
from datetime import UTC, datetime
from unittest import skipUnless

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase, override_settings, tag

from catalog.api.serializers import StreamOverlaySerializer
from catalog.models import Stream
from catalog.services.cache import POLL_LOCK_KEY, reconcile_lock
from catalog.services.observations import MediaTrack, Observation, PathObservation
from catalog.services.reconciliation import record_observation
from catalog.services.streams import delete_absent_streams

DEPLOYED_SERVICES = settings.DATABASES["default"]["ENGINE"].endswith(
    "postgresql"
) and settings.CACHES["default"]["BACKEND"].startswith("django_redis")
needs_deployed_services = skipUnless(
    DEPLOYED_SERVICES, "needs PostgreSQL and Redis (config.settings.integration)"
)
WAIT = 10


def in_thread(work) -> threading.Thread:
    """Runs `work` in a thread of its own, with a database connection of its own."""

    def run():
        try:
            work()
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def edit(stream_id, loaded: threading.Event, go_on: threading.Event, **fields):
    def work():
        copy = Stream.objects.get(pk=stream_id)
        loaded.set()
        go_on.wait(WAIT)
        serializer = StreamOverlaySerializer(copy, data=fields, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

    return work


@tag("integration")
@needs_deployed_services
class ConcurrentWriteTests(TransactionTestCase):
    def test_an_edit_and_a_check_at_the_same_time_keep_each_others_fields(self):
        stream = Stream.objects.create(path_name="live/main")
        loaded, checked = threading.Event(), threading.Event()
        thread = in_thread(edit(stream.pk, loaded, checked, description="Finals at 18:00"))
        self.assertTrue(loaded.wait(WAIT))
        tracks = (MediaTrack(codec="Opus"),)
        observed_at = datetime.now(UTC)
        record_observation(
            Observation(
                observed_at=observed_at,
                paths={"live/main": PathObservation(available=True, tracks=tracks)},
            )
        )
        checked.set()
        thread.join(WAIT)
        stream.refresh_from_db()
        self.assertEqual(stream.description, "Finals at 18:00")
        self.assertEqual(stream.media_kind, Stream.MediaKind.AUDIO)
        self.assertEqual(stream.last_seen_at, observed_at)

    def test_two_organizers_editing_different_fields_keep_both(self):
        stream = Stream.objects.create(path_name="live/main")
        first_loaded, second_loaded, go_on = threading.Event(), threading.Event(), threading.Event()
        threads = [
            in_thread(edit(stream.pk, first_loaded, go_on, display_name="Main stage")),
            in_thread(edit(stream.pk, second_loaded, go_on, description="Finals at 18:00")),
        ]
        self.assertTrue(first_loaded.wait(WAIT) and second_loaded.wait(WAIT))
        go_on.set()
        for thread in threads:
            thread.join(WAIT)
        stream.refresh_from_db()
        self.assertEqual(
            (stream.display_name, stream.description), ("Main stage", "Finals at 18:00")
        )

    def test_deleting_waits_for_no_check_of_the_media_server(self):
        stream = Stream.objects.create(path_name="live/main")
        with reconcile_lock() as acquired:
            self.assertTrue(acquired)
            self.assertEqual(delete_absent_streams([stream.pk]), "busy")
        self.assertTrue(Stream.objects.filter(pk=stream.pk).exists())


@tag("integration")
@needs_deployed_services
class RedisLockTests(SimpleTestCase):
    def setUp(self):
        cache.delete(POLL_LOCK_KEY)

    def acquired_elsewhere(self) -> bool:
        result = []

        def work():
            with reconcile_lock() as acquired:
                result.append(acquired)

        thread = threading.Thread(target=work)
        thread.start()
        thread.join(WAIT)
        return result[0]

    def test_one_process_at_a_time_holds_the_lock(self):
        with reconcile_lock() as acquired:
            self.assertTrue(acquired)
            self.assertFalse(self.acquired_elsewhere())
        self.assertTrue(self.acquired_elsewhere())

    @override_settings(MEDIAMTX_RECONCILE_LOCK_SECONDS=1)
    def test_a_lock_expires_and_releasing_it_late_is_no_error(self):
        with reconcile_lock() as acquired:
            self.assertTrue(acquired)
            time.sleep(1.2)
            self.assertTrue(self.acquired_elsewhere())


@tag("integration")
@needs_deployed_services
class MigrationTests(TransactionTestCase):
    before_media_kinds = (("catalog", "0004_alter_stream_id"),)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_existing_streams_keep_their_rows_and_count_as_seen_when_migrated(self):
        executor = MigrationExecutor(connection)
        executor.migrate(list(self.before_media_kinds))
        old_apps = executor.loader.project_state(list(self.before_media_kinds)).apps
        old_apps.get_model("catalog", "Stream").objects.create(
            path_name="live/old", display_name="Old"
        )
        migrated_at = datetime.now(UTC)
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        stream = Stream.objects.get(path_name="live/old")
        self.assertEqual(stream.display_name, "Old")
        self.assertEqual(stream.media_kind, Stream.MediaKind.UNKNOWN)
        # So that none leaves the catalog before it had the full time to come back.
        self.assertGreaterEqual(stream.last_seen_at, migrated_at)
        self.assertIsNone(Stream.objects.create(path_name="live/new").last_seen_at)
