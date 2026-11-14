from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
from django.conf import settings
from django.contrib import admin
from django.contrib.admin.models import DELETION, LogEntry
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
from catalog.services.cache import (
    SNAPSHOT_KEY,
    read_public_cache,
    reconcile_lock,
    record_failure,
    write_snapshot,
)
from catalog.services.catalog import CatalogService
from catalog.services.media import media_kind_from_tracks
from catalog.services.mediamtx import ActivePath, MediaMTXClient, MediaMTXSchemaError
from catalog.services.playback import build_hls_capture_url, encode_path
from catalog.services.thumbnails import (
    delete_thumbnail,
    get_thumbnail,
    thumbnail_lock,
    thumbnail_needs_refresh,
    write_thumbnail,
)
from catalog.tasks import (
    refresh_mediamtx_snapshot,
    refresh_stream_thumbnail,
    refresh_stream_thumbnails,
)


class MediaServerMixin:
    def reconcile(self, names):
        """Runs a status check that finds `names` live on the media server."""
        with patch("catalog.tasks.MediaMTXClient") as client:
            client.return_value.__enter__.return_value.list_active_paths.return_value = [
                ActivePath(name=name, available=True, tracks2=[{"codec": "H264"}]) for name in names
            ]
            client.return_value.__enter__.return_value.list_configured_paths.return_value = []
            self.assertEqual(refresh_mediamtx_snapshot.run(), "refreshed")

    def organizer(self):
        return get_user_model().objects.create_superuser("owner", "owner@example.org", "password")


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

    def test_codec_names_as_mediamtx_reports_them_decide_the_media_kind(self):
        # Codec names, and the properties that come with them, from MediaMTX 1.21.3 for streams
        # published over RTMP, RTSP and SRT. Several come without dimensions or a sample rate.
        for tracks, media_kind in [
            ([{"codec": "H264", "width": 640, "height": 360}, {"codec": "MPEG-4 Audio"}], "video"),
            ([{"codec": "MPEG-4 Video"}, {"codec": "MPEG-1/2 Audio"}], "video"),
            ([{"codec": "MPEG-1/2 Video"}, {"codec": "AC3", "sample_rate": 48000}], "video"),
            ([{"codec": "M-JPEG"}, {"codec": "Opus", "channel_count": 1}], "video"),
            ([{"codec": "VP8"}], "video"),
            ([{"codec": "VP9"}], "video"),
            ([{"codec": "MPEG-1/2 Audio"}], "audio"),
            ([{"codec": "MPEG-4 Audio LATM"}], "audio"),
            ([{"codec": "G722"}], "audio"),
            ([{"codec": "LPCM", "sample_rate": 48000, "channel_count": 1}], "audio"),
        ]:
            with self.subTest(tracks=tracks):
                self.assertEqual(media_kind_from_tracks(tracks), media_kind)

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
        self.assertContains(response, reverse("catalog-api:stream-detail", args=[stream.id]))
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
        cache.delete(SNAPSHOT_KEY)
        self.assertEqual(listed(), ["live/recent"])
        self.reconcile(["live/old"])
        self.assertEqual(listed(), ["live/old", "live/recent"])
        old.refresh_from_db()
        self.assertGreater(old.last_seen_at, long_ago)
        Stream.objects.filter(path_name="live/old").update(last_seen_at=long_ago)
        self.reconcile([])
        with self.settings(STREAM_HIDE_OFFLINE_AFTER_DAYS=0):
            self.assertEqual(listed(), ["live/old", "live/recent"])

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

    def test_thumbnail_scan_gives_the_capture_worker_the_stream_details(self):
        self.reconcile(["live/video"])
        stream = Stream.objects.get(path_name="live/video")
        with patch.object(refresh_stream_thumbnail, "apply_async") as apply_async:
            self.assertEqual(refresh_stream_thumbnails.run(), "queued:1")
        apply_async.assert_called_once_with(
            args=(str(stream.id), "live/video", "video"),
            expires=settings.STREAM_THUMBNAIL_TASK_EXPIRES_SECONDS,
        )


# The admin is in LANGUAGE_CODE, as i18n_patterns doesn't cover it.
@override_settings(LANGUAGE_CODE="en")
class StreamAdminTests(MediaServerMixin, TestCase):
    changelist = "admin:catalog_stream_changelist"

    def setUp(self):
        cache.clear()

    def run_action(self, action, streams):
        return self.client.post(
            reverse(self.changelist),
            {"action": action, "_selected_action": [str(stream.pk) for stream in streams]},
            follow=True,
        )

    def test_list_shows_hidden_streams_as_hidden(self):
        live = Stream.objects.create(path_name="live/main")
        hidden = Stream.objects.create(path_name="live/hidden")
        BlockedPath.objects.create(path_name=hidden.path_name)
        self.reconcile([live.path_name, hidden.path_name])
        self.client.force_login(self.organizer())
        response = self.client.get(reverse(self.changelist))
        statuses = {
            stream.path_name: stream.catalog_status for stream in response.context["cl"].result_list
        }
        self.assertEqual(statuses, {"live/main": "live", "live/hidden": "hidden"})
        self.assertContains(response, '<td class="field-status">Hidden</td>', html=True)
        self.assertContains(response, '<td class="field-status">Live</td>', html=True)

    def test_deleting_a_stream_needs_a_recent_check_that_its_path_is_gone(self):
        stream = Stream.objects.create(path_name="live/absent")
        model_admin = StreamAdmin(Stream, admin.site)
        request = RequestFactory().get("/admin/")
        request.user = self.organizer()
        self.assertFalse(model_admin.has_delete_permission(request, stream))
        self.reconcile([])
        self.assertTrue(model_admin.has_delete_permission(request, stream))
        record_failure("timeout")
        self.assertFalse(model_admin.has_delete_permission(request, stream))
        self.reconcile([])
        with self.settings(MEDIAMTX_FRESHNESS_SECONDS=0):
            self.assertFalse(model_admin.has_delete_permission(request, stream))
        BlockedPath.objects.create(path_name=stream.path_name)
        self.assertFalse(model_admin.has_delete_permission(request, stream))
        BlockedPath.objects.all().delete()
        self.reconcile([stream.path_name])
        self.assertFalse(model_admin.has_delete_permission(request, stream))

    def test_bulk_delete_removes_only_streams_the_media_server_no_longer_has(self):
        present = Stream.objects.create(path_name="live/present")
        absent = Stream.objects.create(path_name="live/absent")
        hidden = Stream.objects.create(path_name="live/hidden")
        BlockedPath.objects.create(path_name=hidden.path_name)
        self.client.force_login(self.organizer())
        selection = [present, absent, hidden]
        response = self.run_action("delete_absent_streams", selection)
        self.assertEqual(Stream.objects.count(), 3)
        self.assertContains(response, "Nothing was deleted")
        self.reconcile([present.path_name])
        response = self.run_action("delete_absent_streams", selection)
        self.assertEqual(
            sorted(Stream.objects.values_list("path_name", flat=True)),
            ["live/hidden", "live/present"],
        )
        self.assertEqual(
            [str(message) for message in response.context["messages"]],
            [
                "Deleted 1 stream.",
                "Kept, as the media server still has them: present",
                "Kept, as hidden streams aren't checked on the media server: hidden. "
                "Unhide them, then delete them after the next check.",
            ],
        )
        self.assertTrue(
            LogEntry.objects.filter(action_flag=DELETION, object_id=str(absent.pk)).exists()
        )

    def test_actions_are_offered_by_permission(self):
        user = get_user_model().objects.create_user("staff", password="password", is_staff=True)
        user.user_permissions.add(
            *Permission.objects.filter(codename__in=["view_stream", "change_stream"])
        )
        self.client.force_login(user)

        def offered():
            form = self.client.get(reverse(self.changelist)).context["action_form"]
            return set() if form is None else {name for name, _ in form.fields["action"].choices}

        self.assertEqual(offered(), set())
        user.user_permissions.add(
            *Permission.objects.filter(codename__in=["add_blockedpath", "delete_blockedpath"])
        )
        self.assertEqual(offered(), {"", "hide_streams", "unhide_streams"})
        user.user_permissions.add(Permission.objects.get(codename="delete_stream"))
        self.assertEqual(offered(), {"", "hide_streams", "unhide_streams", "delete_absent_streams"})

    def test_unhide_shows_hidden_streams_again(self):
        stream = Stream.objects.create(path_name="live/hidden")
        BlockedPath.objects.create(path_name=stream.path_name)
        self.client.force_login(self.organizer())
        response = self.run_action("unhide_streams", [stream])
        self.assertFalse(BlockedPath.objects.filter(path_name=stream.path_name).exists())
        self.assertContains(response, "Selected streams are no longer hidden.")
        self.assertEqual(CatalogService().get(stream.id)[1].path_name, stream.path_name)


PROXY = "172.31.241.1"


@override_settings(TRUSTED_PROXIES=[PROXY])
class AdminLoginLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser("organizer", "organizer@example.org", "secret")

    def log_in(self, password, **meta):
        return self.client.post(
            "/admin/login/", {"username": "organizer", "password": password}, **meta
        )

    def test_failures_lock_the_username_only_for_the_address_they_came_from(self):
        for _ in range(4):
            self.assertEqual(self.log_in("wrong", REMOTE_ADDR="198.51.100.7").status_code, 200)
        self.assertEqual(self.log_in("wrong", REMOTE_ADDR="198.51.100.7").status_code, 429)
        self.assertEqual(self.log_in("secret", REMOTE_ADDR="198.51.100.7").status_code, 429)
        self.assertEqual(self.log_in("secret", REMOTE_ADDR="203.0.113.9").status_code, 302)

    def test_clients_behind_the_trusted_proxy_are_counted_separately(self):
        for _ in range(5):
            self.log_in("wrong", HTTP_X_FORWARDED_FOR="198.51.100.7", REMOTE_ADDR=PROXY)
        locked = self.log_in("secret", HTTP_X_FORWARDED_FOR="198.51.100.7", REMOTE_ADDR=PROXY)
        self.assertEqual(locked.status_code, 429)
        # The proxy appends the address it served; earlier entries come from the client.
        forged = self.log_in(
            "secret", HTTP_X_FORWARDED_FOR="203.0.113.9, 198.51.100.7", REMOTE_ADDR=PROXY
        )
        self.assertEqual(forged.status_code, 429)
        other = self.log_in("secret", HTTP_X_FORWARDED_FOR="203.0.113.9", REMOTE_ADDR=PROXY)
        self.assertEqual(other.status_code, 302)

    def test_x_forwarded_for_from_other_addresses_is_ignored(self):
        for index in range(5):
            self.log_in(
                "wrong", REMOTE_ADDR="198.51.100.7", HTTP_X_FORWARDED_FOR=f"203.0.113.{index}"
            )
        response = self.log_in(
            "secret", REMOTE_ADDR="198.51.100.7", HTTP_X_FORWARDED_FOR="203.0.113.50"
        )
        self.assertEqual(response.status_code, 429)

    def test_logins_fail_while_the_cache_is_down(self):
        with (
            patch("django.core.cache.cache.get", side_effect=RedisConnectionError("unavailable")),
            self.assertRaises(RedisConnectionError),
        ):
            self.log_in("secret", REMOTE_ADDR="198.51.100.7")


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
        for lock_context in [reconcile_lock, lambda: thumbnail_lock("id")]:
            with self.subTest(lock=lock_context), patch("catalog.services.cache.cache") as mocked:
                lock = MagicMock()
                lock.acquire.return_value = True
                lock.release.side_effect = LockNotOwnedError("expired")
                mocked.lock.return_value = lock
                with lock_context() as acquired:
                    self.assertTrue(acquired)

    def test_cache_outage_does_not_allow_worker_lock_bypass(self):
        for lock_context in [reconcile_lock, lambda: thumbnail_lock("id")]:
            with self.subTest(lock=lock_context), patch("catalog.services.cache.cache") as mocked:
                mocked.lock.return_value.acquire.side_effect = RedisConnectionError("down")
                with self.assertRaises(RedisConnectionError), lock_context():
                    self.fail("Lock acquisition must not succeed while Redis is down")

    def test_capture_needs_no_database(self):
        # SimpleTestCase fails any query, like the capture worker's settings.
        write_snapshot(
            {
                "observed_at": datetime.now(UTC).isoformat(),
                "paths": {"live/video": {"available": True, "tracks": [{"codec": "H264"}]}},
            }
        )
        with patch("catalog.tasks.capture_thumbnail", return_value=b"a frame") as capture:
            self.assertEqual(refresh_stream_thumbnail.run("id", "live/video", "video"), "captured")
        capture.assert_called_once_with(build_hls_capture_url("live/video"))
        self.assertEqual(get_thumbnail("id").content, b"a frame")

    def test_cache_errors_outside_transport_failures_are_not_hidden(self):
        with (
            patch("catalog.services.cache.cache.get", side_effect=ValueError("bad serializer")),
            self.assertRaises(ValueError),
        ):
            read_public_cache("key")

    def test_cache_read_warning_sanitizes_key_without_changing_cache_lookup(self):
        key = "cache\r\nkey"
        with (
            patch(
                "catalog.services.cache.cache.get",
                side_effect=RedisConnectionError("unavailable"),
            ) as cache_get,
            self.assertLogs("catalog.services.cache", level="WARNING") as logs,
        ):
            self.assertIsNone(read_public_cache(key))

        cache_get.assert_called_once_with(key)
        self.assertNotIn("\r", logs.output[0])
        self.assertNotIn("\n", logs.output[0])

    @override_settings(STREAM_THUMBNAIL_REFRESH_SECONDS=60)
    def test_old_thumbnail_is_refreshed(self):
        with patch("catalog.services.thumbnails.datetime") as clock:
            clock.now.return_value = datetime.now(UTC) - timedelta(minutes=2)
            write_thumbnail("old", b"old image")
        self.assertTrue(thumbnail_needs_refresh("old"))
