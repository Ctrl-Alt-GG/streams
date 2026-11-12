from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from pydantic import ValidationError as PydanticValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import LockNotOwnedError
from redis.exceptions import TimeoutError as RedisTimeoutError

from catalog.admin import StreamAdmin
from catalog.models import BlockedPath, Stream
from catalog.services.cache import SNAPSHOT_KEY, read_public_cache, reconcile_lock, write_snapshot
from catalog.services.catalog import CatalogService
from catalog.services.media import media_kind_from_tracks
from catalog.services.mediamtx import ActivePath, MediaMTXClient, MediaMTXSchemaError
from catalog.services.playback import encode_path
from catalog.services.thumbnails import (
    delete_thumbnail,
    thumbnail_lock,
    thumbnail_needs_refresh,
    write_thumbnail,
)
from catalog.tasks import refresh_mediamtx_snapshot


class StreamPathTests(SimpleTestCase):
    def test_rejects_paths_that_cannot_safely_form_a_playback_url(self):
        for name in [
            "live/..",
            "live/.",
            "/live/x",
            "live/",
            "live//x",
            "a" * 513,
            "live/\\x",
            "live/\x00",
        ]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                encode_path(name)
        self.assertEqual(encode_path("live/a b"), "live/a%20b")

    def test_video_codec_is_sufficient_without_dimensions(self):
        self.assertEqual(media_kind_from_tracks([{"codec": "H264"}, {"codec": "Opus"}]), "video")
        self.assertEqual(media_kind_from_tracks([{"codec": "H265"}]), "video")
        self.assertEqual(media_kind_from_tracks([{"codec": "MPEG4Audio"}]), "audio")
        self.assertEqual(media_kind_from_tracks([None, {"codec": "AV1"}]), "video")
        self.assertEqual(media_kind_from_tracks([{"codec": "not-a-known-codec"}]), "unknown")

    def test_old_mediamtx_schema_is_reported_instead_of_showing_every_stream_offline(self):
        with self.assertRaises(PydanticValidationError):
            ActivePath.model_validate({"name": "live/old", "ready": True})
        client = httpx.Client(
            base_url="https://media.example/",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "itemCount": 1,
                        "pageCount": 1,
                        "items": [{"name": "old", "ready": True}],
                    },
                )
            ),
        )
        with client, self.assertRaises(MediaMTXSchemaError):
            MediaMTXClient(client).list_active_paths()

    def test_server_error_template_does_not_need_request_or_static_storage(self):
        with patch(
            "django.contrib.staticfiles.storage.staticfiles_storage.url", side_effect=ValueError
        ):
            self.assertIn("Streams", render_to_string("500.html"))


class ReconciliationTests(TestCase):
    def setUp(self):
        cache.clear()

    def reconcile(self, names):
        with patch("catalog.tasks.MediaMTXClient") as client:
            client.return_value.__enter__.return_value.list_active_paths.return_value = [
                ActivePath(name=name, available=True, tracks2=[{"codec": "H264"}]) for name in names
            ]
            client.return_value.__enter__.return_value.list_configured_paths.return_value = []
            self.assertEqual(refresh_mediamtx_snapshot.run(), "refreshed")

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

    def test_legacy_bad_rows_do_not_break_catalog_or_api(self):
        Stream.objects.bulk_create([Stream(path_name="live/.."), Stream(path_name="live/good")])
        self.reconcile(["live/good"])
        self.assertEqual(self.client.get("/").status_code, 200)
        response = self.client.get("/api/v1/streams/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 1)

    def test_detail_lookup_does_not_project_entire_catalog(self):
        good = Stream.objects.create(path_name="live/good")
        with patch.object(
            CatalogService, "list", side_effect=AssertionError("whole catalog loaded")
        ):
            self.assertEqual(CatalogService().get(good.id)[1].id, str(good.id))

    def test_hidden_stream_cannot_be_retrieved(self):
        stream = Stream.objects.create(path_name="live/hidden")
        BlockedPath.objects.create(path_name=stream.path_name)
        with self.assertRaises(Stream.DoesNotExist):
            CatalogService().get(stream.id)

    def test_polling_markup_and_local_player_script(self):
        stream = Stream.objects.create(path_name="live/radio", media_kind=Stream.MediaKind.AUDIO)
        write_snapshot(
            {
                "observed_at": datetime.now(UTC).isoformat(),
                "paths": {
                    stream.path_name: {"available": True, "tracks": [{"codec": "Opus"}]},
                },
            }
        )
        response = self.client.get(reverse("catalog:stream-detail", args=[stream.id]))
        self.assertContains(response, 'id="catalog-watch-state"')
        self.assertContains(response, "/static/catalog/js/hls.min.js")
        self.assertNotContains(response, "https://streams.invalid/hls/live/radio/hls.min.js")

    def test_overlay_validation_errors_answer_400(self):
        user = get_user_model().objects.create_superuser(
            "organizer", "organizer@example.org", "password"
        )
        self.client.force_login(user)
        stream = Stream.objects.create(path_name="live/test")
        with patch.object(
            Stream, "save", side_effect=ValidationError({"display_name": ["Invalid"]})
        ):
            response = self.client.patch(
                f"/api/v1/streams/{stream.id}/",
                {"display_name": "new"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 400)

    def test_staff_downgrade_revokes_existing_session_write_access(self):
        user = get_user_model().objects.create_user("staff", password="password", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="change_stream"))
        stream = Stream.objects.create(path_name="live/test")
        self.client.force_login(user)
        url = f"/api/v1/streams/{stream.id}/"
        response = self.client.patch(
            url, {"display_name": "before"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        get_user_model().objects.filter(pk=user.pk).update(is_staff=False)
        response = self.client.patch(
            url, {"display_name": "after"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)
        stream.refresh_from_db()
        self.assertEqual(stream.display_name, "before")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(user.user_permissions.filter(codename="change_stream").exists())

    def test_staff_status_without_model_permission_cannot_edit(self):
        user = get_user_model().objects.create_user("staff", password="password", is_staff=True)
        self.client.force_login(user)
        stream = Stream.objects.create(path_name="live/test")
        response = self.client.patch(
            f"/api/v1/streams/{stream.id}/",
            {"display_name": "new"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_redis_transport_failure_keeps_public_views_and_thumbnails_available(self):
        stream = Stream.objects.create(path_name="live/radio", media_kind=Stream.MediaKind.AUDIO)
        detail_url = reverse("catalog:stream-detail", args=[stream.id])
        thumbnail_url = reverse("catalog:stream-thumbnail", args=[stream.id])
        for error_type in (RedisConnectionError, RedisTimeoutError):
            with (
                self.subTest(error_type=error_type),
                patch("catalog.services.cache.cache.get", side_effect=error_type("unavailable")),
                self.assertLogs("catalog.services.cache", level="WARNING"),
            ):
                self.assertEqual(self.client.get("/").status_code, 200)
                response = self.client.get(detail_url)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["stream"].audio_only)
                self.assertEqual(response.context["source"].status, "unavailable")
                response = self.client.get("/api/v1/streams/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["source"]["status"], "unavailable")
                self.assertEqual(response.json()["results"][0]["status"], "unknown")
                self.assertTrue(response.json()["results"][0]["audio_only"])
                response = self.client.get(f"/api/v1/streams/{stream.id}/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["result"]["status"], "unknown")
                response = self.client.get(thumbnail_url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/static/catalog/thumbnails/", response.headers["Location"])

    def test_audio_identity_survives_missing_expired_and_restored_snapshots(self):
        stream = Stream.objects.create(path_name="live/radio", media_kind=Stream.MediaKind.AUDIO)
        source, projection = CatalogService().get(stream.id)
        self.assertEqual(source.status, "unavailable")
        self.assertTrue(projection.audio_only)
        snapshot = {
            "observed_at": datetime.now(UTC).isoformat(),
            "paths": {stream.path_name: {"available": True, "tracks": [{"codec": "Opus"}]}},
        }
        write_snapshot(snapshot)
        self.assertEqual(CatalogService().get(stream.id)[1].status, "live")
        cache.set(SNAPSHOT_KEY, {**snapshot, "schema_version": 1}, timeout=-1)
        self.assertTrue(CatalogService().get(stream.id)[1].audio_only)
        self.assertEqual(CatalogService().get(stream.id)[1].status, "unknown")
        write_snapshot(snapshot)
        source, projection = CatalogService().get(stream.id)
        self.assertEqual(source.status, "fresh")
        self.assertTrue(projection.audio_only)
        self.assertEqual(projection.status, "live")

    @override_settings(
        PUBLIC_BASE_URL="http://testserver", MEDIAMTX_HLS_PUBLIC_BASE_URL="http://testserver/hls"
    )
    def test_same_origin_video_loads_media_through_local_player_without_iframe(self):
        stream = Stream.objects.create(path_name="live/video", media_kind=Stream.MediaKind.VIDEO)
        self.reconcile([stream.path_name])
        response = self.client.get(reverse("catalog:stream-detail", args=[stream.id]))
        self.assertContains(response, "<video")
        self.assertContains(response, 'data-source="http://testserver/hls/live/video/index.m3u8"')
        self.assertContains(response, "/static/catalog/js/hls.min.js")
        self.assertContains(response, "/static/catalog/js/player.js")
        self.assertNotContains(response, "<iframe")
        self.assertIn("frame-src 'none'", response.headers["Content-Security-Policy"])

    def test_admin_login_is_throttled(self):
        for _ in range(30):
            self.client.post("/admin/login/", {"username": "missing", "password": "invalid"})
        response = self.client.post("/admin/login/", {"username": "missing", "password": "invalid"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "60")

    def test_absent_stream_can_be_deleted_only_with_permission_and_fresh_snapshot(self):
        from django.contrib import admin

        stream = Stream.objects.create(path_name="live/absent")
        organizer = get_user_model().objects.create_superuser(
            "owner", "owner@example.org", "password"
        )
        request = RequestFactory().get("/admin/")
        request.user = organizer
        model_admin = StreamAdmin(Stream, admin.site)
        self.assertFalse(model_admin.has_delete_permission(request, stream))
        self.reconcile([])
        self.assertTrue(model_admin.has_delete_permission(request, stream))


class ThumbnailCacheTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_freshness_reads_metadata_without_loading_image(self):
        with patch("catalog.services.thumbnails.get_thumbnail", side_effect=AssertionError):
            self.assertTrue(thumbnail_needs_refresh("stream"))
            write_thumbnail("stream", b"a frame")
            self.assertFalse(thumbnail_needs_refresh("stream"))
            delete_thumbnail("stream")
            self.assertTrue(thumbnail_needs_refresh("stream"))

    def test_expired_locks_do_not_fail_completed_work(self):
        for lock_context, target in [
            (reconcile_lock, "catalog.services.cache.cache"),
            (lambda: thumbnail_lock("id"), "catalog.services.thumbnails.cache"),
        ]:
            with self.subTest(target=target), patch(target) as mocked_cache:
                lock = MagicMock()
                lock.acquire.return_value = True
                lock.release.side_effect = LockNotOwnedError("expired")
                mocked_cache.lock.return_value = lock
                with lock_context() as acquired:
                    self.assertTrue(acquired)

    def test_cache_outage_does_not_allow_login_or_worker_lock_bypass(self):
        from django.http import HttpResponse

        from catalog.middleware import AdminLoginThrottleMiddleware

        request = RequestFactory().post("/admin/login/")
        middleware = AdminLoginThrottleMiddleware(lambda _request: HttpResponse("should not pass"))
        with (
            patch("catalog.middleware.cache.add", side_effect=RedisConnectionError("unavailable")),
            self.assertRaises(RedisConnectionError),
        ):
            middleware(request)
        for lock_context, target in [
            (reconcile_lock, "catalog.services.cache.cache"),
            (lambda: thumbnail_lock("id"), "catalog.services.thumbnails.cache"),
        ]:
            with self.subTest(target=target), patch(target) as mocked_cache:
                mocked_cache.lock.return_value.acquire.side_effect = RedisConnectionError("down")
                with self.assertRaises(RedisConnectionError), lock_context():
                    self.fail("Lock acquisition must not succeed while Redis is down")

    def test_cache_errors_outside_transport_failures_are_not_hidden(self):
        with (
            patch("catalog.services.cache.cache.get", side_effect=ValueError("bad serializer")),
            self.assertRaises(ValueError),
        ):
            read_public_cache("key")

    @override_settings(STREAM_THUMBNAIL_REFRESH_SECONDS=60)
    def test_old_thumbnail_is_refreshed(self):
        with patch("catalog.services.thumbnails.datetime") as clock:
            clock.now.return_value = datetime.now(UTC) - timedelta(minutes=2)
            write_thumbnail("old", b"old image")
        self.assertTrue(thumbnail_needs_refresh("old"))
