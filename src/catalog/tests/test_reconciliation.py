from unittest.mock import patch

import httpx
from django.core.cache import cache
from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse

from catalog.models import Stream
from catalog.services.cache import reconcile_lock
from catalog.services.catalog import CatalogService
from catalog.services.observations import observation_state, read_health, read_observation
from catalog.services.reconciliation import reconcile_media_server
from catalog.tests.support import FakeMediaServer, MediaServerMixin, live_video


class ReconciliationTests(MediaServerMixin, TestCase):
    def setUp(self):
        cache.clear()

    def test_reconnect_preserves_stream_id_and_offline_page(self):
        self.reconcile(["live/stable"])
        first = Stream.objects.get(path_name="live/stable")
        self.reconcile([])
        source, offline = CatalogService().get(first.id)
        self.assertEqual(source.status, "fresh")
        self.assertEqual(offline.status, "offline")
        self.assertEqual(
            self.client.get(reverse("catalog:stream-detail", args=[first.id])).status_code, 200
        )
        self.reconcile(["live/stable"])
        self.assertEqual(Stream.objects.get(path_name="live/stable").id, first.id)

    def test_bad_names_do_not_stop_a_healthy_stream(self):
        self.reconcile(["live/..", "a" * 513, "live/good"])
        self.assertEqual(list(Stream.objects.values_list("path_name", flat=True)), ["live/good"])
        self.assertEqual(CatalogService().list().streams[0].status, "live")

    def test_a_failed_check_keeps_the_last_complete_one(self):
        self.reconcile(["live/main"])
        complete = read_observation()
        server = FakeMediaServer([], error=httpx.ConnectError("refused"))
        with self.assertLogs("catalog.services.reconciliation", level="WARNING"):
            outcome = reconcile_media_server(client_factory=lambda: server)
        self.assertEqual(outcome, "failed")
        self.assertEqual(read_observation(), complete)
        self.assertEqual(observation_state().status, "stale")
        self.assertEqual(read_health().reason, "transport")
        self.assertEqual(read_health().consecutive_failures, 1)
        self.reconcile(["live/main"])
        self.assertEqual(observation_state().status, "fresh")
        self.assertEqual(read_health().consecutive_failures, 0)

    def test_a_check_whose_database_writes_fail_publishes_nothing(self):
        server = FakeMediaServer([live_video("live/main")])
        with (
            patch(
                "catalog.services.reconciliation.record_observation",
                side_effect=DatabaseError("connection lost"),
            ),
            self.assertLogs("catalog.services.reconciliation", level="WARNING"),
        ):
            outcome = reconcile_media_server(client_factory=lambda: server)
        self.assertEqual(outcome, "failed")
        self.assertIsNone(read_observation())
        self.assertEqual(read_health().reason, "unknown")

    def test_a_check_while_another_is_under_way_does_nothing(self):
        server = FakeMediaServer([live_video("live/main")])
        with reconcile_lock() as acquired:
            self.assertTrue(acquired)
            self.assertEqual(reconcile_media_server(client_factory=lambda: server), "locked")
        self.assertFalse(Stream.objects.exists())

    def test_a_stream_deleted_meanwhile_is_created_again_without_failing_the_check(self):
        self.reconcile(["live/main"])
        Stream.objects.all().delete()
        self.reconcile(["live/main"])
        self.assertTrue(Stream.objects.filter(path_name="live/main").exists())

    def test_settings_paths_and_patterns_of_the_configuration_are_not_channels(self):
        from catalog.services.mediamtx import ConfiguredPath

        server = FakeMediaServer(
            configured=[
                ConfiguredPath(name=name) for name in ["all", "all_others", "~^live/.*$", "rec"]
            ]
        )
        self.reconcile([], server)
        self.assertEqual(list(Stream.objects.values_list("path_name", flat=True)), ["rec"])
