import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from catalog.models import BlockedPath, Stream
from catalog.services.catalog import CatalogService
from catalog.services.observations import OBSERVATION_KEY
from catalog.services.thumbnails import write_thumbnail
from catalog.tests.support import FakeMediaServer, MediaServerMixin, live_video, observe


class CatalogTests(MediaServerMixin, TestCase):
    def setUp(self):
        cache.clear()

    def test_legacy_bad_rows_do_not_break_catalog_or_api(self):
        Stream.objects.bulk_create([Stream(path_name="live/.."), Stream(path_name="live/good")])
        self.reconcile(["live/good"])
        self.assertEqual(self.client.get("/").status_code, 200)
        response = self.client.get("/api/v1/streams/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 1)

    def test_one_channel_with_odd_track_properties_does_not_break_the_list(self):
        server = FakeMediaServer(
            [live_video("live/odd", width="broken", height=360), live_video("live/good")]
        )
        self.reconcile([], server)
        response = self.client.get("/api/v1/streams/")
        self.assertEqual(response.status_code, 200)
        odd = next(item for item in response.json()["results"] if item["effective_name"] == "odd")
        self.assertEqual(
            odd["tracks"],
            [
                {
                    "codec": "H264",
                    "width": None,
                    "height": 360,
                    "sample_rate": None,
                    "channel_count": None,
                },
            ],
        )

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

    def test_pages_name_the_fields_they_watch_and_load_the_local_player(self):
        stream = Stream.objects.create(path_name="live/radio", media_kind=Stream.MediaKind.AUDIO)
        observe({stream.path_name: {"available": True, "tracks": [{"codec": "Opus"}]}})
        response = self.client.get(reverse("catalog:stream-detail", args=[stream.id]))
        watch_state = json.loads(
            response.content.decode()
            .split('id="catalog-watch-state" type="application/json">')[1]
            .split("</script>")[0]
        )
        self.assertEqual(
            watch_state,
            {
                "url": reverse("catalog-api:stream-detail", args=[stream.id]),
                "fields": ["id", "status", "audio_only", "effective_name", "description"],
                "streams": [
                    {
                        "id": str(stream.id),
                        "status": "live",
                        "audio_only": True,
                        "effective_name": "radio",
                        "description": "",
                    }
                ],
            },
        )
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

    def test_audio_identity_survives_missing_expired_and_restored_observations(self):
        stream = Stream.objects.create(path_name="live/radio", media_kind=Stream.MediaKind.AUDIO)
        source, projection = CatalogService().get(stream.id)
        self.assertEqual(source.status, "unavailable")
        self.assertTrue(projection.audio_only)
        paths = {stream.path_name: {"available": True, "tracks": [{"codec": "Opus"}]}}
        observe(paths)
        self.assertEqual(CatalogService().get(stream.id)[1].status, "live")
        cache.delete(OBSERVATION_KEY)
        self.assertTrue(CatalogService().get(stream.id)[1].audio_only)
        self.assertEqual(CatalogService().get(stream.id)[1].status, "unknown")
        observe(paths)
        source, projection = CatalogService().get(stream.id)
        self.assertEqual(source.status, "fresh")
        self.assertTrue(projection.audio_only)
        self.assertEqual(projection.status, "live")

    def test_an_unreadable_observation_counts_as_none(self):
        stream = Stream.objects.create(path_name="live/main")
        cache.set(OBSERVATION_KEY, {"observed_at": "yesterday", "paths": []})
        source, projection = CatalogService().get(stream.id)
        self.assertEqual((source.status, projection.status), ("unavailable", "unknown"))

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

    @override_settings(STREAM_HIDE_OFFLINE_AFTER_DAYS=14)
    def test_channels_not_live_for_long_leave_the_catalog_but_keep_their_page(self):
        def listed(**options):
            return sorted(stream.path_name for stream in CatalogService().list(**options).streams)

        self.reconcile(["live/old", "live/recent"])
        long_ago = datetime.now(UTC) - timedelta(days=15)
        Stream.objects.filter(path_name="live/old").update(last_seen_at=long_ago)
        self.reconcile([])
        self.assertEqual(listed(), ["live/recent"])
        self.assertEqual(listed(include_unseen=True), ["live/old", "live/recent"])
        old = Stream.objects.get(path_name="live/old")
        self.assertEqual(
            self.client.get(reverse("catalog:stream-detail", args=[old.id])).status_code, 200
        )
        cache.delete(OBSERVATION_KEY)
        self.assertEqual(listed(), ["live/recent"])
        self.reconcile(["live/old"])
        self.assertEqual(listed(), ["live/old", "live/recent"])
        old.refresh_from_db()
        self.assertGreater(old.last_seen_at, long_ago)
        Stream.objects.filter(path_name="live/old").update(last_seen_at=long_ago)
        self.reconcile([])
        with self.settings(STREAM_HIDE_OFFLINE_AFTER_DAYS=0):
            self.assertEqual(listed(), ["live/old", "live/recent"])

    @override_settings(STREAM_HIDE_OFFLINE_AFTER_DAYS=14)
    def test_channels_left_out_of_the_catalog_cost_no_projection(self):
        long_ago = datetime.now(UTC) - timedelta(days=30)
        Stream.objects.bulk_create([Stream(path_name=f"live/old-{index}") for index in range(200)])
        Stream.objects.update(created_at=long_ago, last_seen_at=long_ago)
        Stream.objects.create(path_name="live/recent")
        Stream.objects.filter(path_name="live/old-7").update(created_at=long_ago)
        self.reconcile(["live/old-7"])
        original = CatalogService._project
        with patch.object(
            CatalogService, "_project", autospec=True, side_effect=original
        ) as project:
            streams = CatalogService().list().streams
        self.assertEqual(
            sorted(stream.path_name for stream in streams), ["live/old-7", "live/recent"]
        )
        self.assertEqual(project.call_count, 2)

    def test_thumbnail_answers_conditional_requests_with_its_cache_headers(self):
        stream = Stream.objects.create(path_name="live/video")
        thumbnail = write_thumbnail(stream.id, b"a frame")
        url = reverse("catalog:stream-thumbnail", args=[stream.id])
        response = self.client.get(url)
        self.assertEqual(response.content, b"a frame")
        self.assertEqual(response.headers["ETag"], f'"{thumbnail.etag}"')
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        max_age = min(30, settings.STREAM_THUMBNAIL_REFRESH_SECONDS)
        self.assertEqual(response.headers["Cache-Control"], f"public, max-age={max_age}")
        response = self.client.get(url, headers={"if-none-match": f'"{thumbnail.etag}"'})
        self.assertEqual(response.status_code, 304)
        self.assertEqual(response.headers["ETag"], f'"{thumbnail.etag}"')
        self.assertEqual(response.headers["Cache-Control"], f"public, max-age={max_age}")

    @override_settings(PUBLIC_BASE_URL="https://streams.example.com")
    def test_pages_address_artwork_on_their_own_host_and_the_api_on_the_public_one(self):
        stream = Stream.objects.create(path_name="live/quiet")
        self.reconcile([])
        response = self.client.get("/")
        self.assertContains(response, 'src="/static/catalog/thumbnails/')
        self.assertNotContains(response, "https://streams.example.com/static/")
        api_thumbnail = self.client.get("/api/v1/streams/").json()["results"][0]["thumbnail_url"]
        self.assertTrue(
            api_thumbnail.startswith("https://streams.example.com/static/catalog/thumbnails/")
        )
        response = self.client.get(reverse("catalog:stream-thumbnail", args=[stream.id]))
        self.assertTrue(response.headers["Location"].startswith("/static/catalog/thumbnails/"))
