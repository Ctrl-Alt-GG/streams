from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import LockNotOwnedError

from catalog.models import Stream
from catalog.services.cache import read_public_cache, reconcile_lock
from catalog.services.playback import build_hls_capture_url
from catalog.services.thumbnails import (
    delete_thumbnail,
    desired_thumbnail_kind,
    get_thumbnail,
    thumbnail_lock,
    thumbnail_needs_refresh,
    write_thumbnail,
)
from catalog.tasks import refresh_stream_thumbnail, refresh_stream_thumbnails
from catalog.tests.support import MediaServerMixin, observe


def written_at(moment: datetime, stream_id, content=b"a frame"):
    with patch("catalog.services.thumbnails.datetime") as clock:
        clock.now.return_value = moment
        write_thumbnail(stream_id, content)


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
        observe({"live/video": {"available": True, "tracks": [{"codec": "H264"}]}})
        with patch("catalog.tasks.capture_thumbnail", return_value=b"a frame") as capture:
            self.assertEqual(refresh_stream_thumbnail.run("id", "live/video", "video"), "captured")
        capture.assert_called_once_with(build_hls_capture_url("live/video"))
        self.assertEqual(get_thumbnail("id").content, b"a frame")

    def test_a_thumbnail_shows_a_frame_of_live_video_only(self):
        observation = observe(
            {
                "live/video": {"available": True, "tracks": [{"codec": "H264"}]},
                "live/radio": {"available": True, "tracks": [{"codec": "Opus"}]},
                "live/off": {"available": False},
            }
        )
        for path_name, media_kind, kind in [
            ("live/video", "unknown", "frame"),
            ("live/radio", "unknown", "audio"),
            ("live/off", "video", "offline"),
            ("live/off", "audio", "audio"),
            ("live/missing", "video", "fallback"),
        ]:
            with self.subTest(path_name=path_name, media_kind=media_kind):
                self.assertEqual(desired_thumbnail_kind(observation, path_name, media_kind), kind)
        self.assertEqual(desired_thumbnail_kind(None, "live/video", "audio"), "audio")
        self.assertEqual(desired_thumbnail_kind(None, "live/video", "video"), "fallback")

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
        written_at(datetime.now(UTC) - timedelta(minutes=2), "old", b"old image")
        self.assertTrue(thumbnail_needs_refresh("old"))


class ThumbnailScanTests(MediaServerMixin, TestCase):
    def setUp(self):
        cache.clear()

    def test_thumbnail_scan_gives_the_capture_worker_the_stream_details(self):
        self.reconcile(["live/video"])
        stream = Stream.objects.get(path_name="live/video")
        with patch.object(refresh_stream_thumbnail, "apply_async") as apply_async:
            self.assertEqual(refresh_stream_thumbnails.run(), "queued:1")
        apply_async.assert_called_once_with(
            args=(str(stream.id), "live/video", "video"),
            expires=settings.STREAM_THUMBNAIL_TASK_EXPIRES_SECONDS,
        )

    @override_settings(STREAM_THUMBNAIL_CAPTURES_PER_SCAN=2, STREAM_THUMBNAIL_REFRESH_SECONDS=60)
    def test_a_scan_queues_the_oldest_thumbnails_first_and_no_more_than_get_captured(self):
        self.reconcile(["live/a", "live/b", "live/c", "live/d"])
        streams = {stream.path_name: stream for stream in Stream.objects.all()}
        now = datetime.now(UTC)
        written_at(now - timedelta(minutes=5), streams["live/a"].id)
        written_at(now - timedelta(minutes=2), streams["live/c"].id)
        written_at(now, streams["live/d"].id)
        with patch.object(refresh_stream_thumbnail, "apply_async") as apply_async:
            self.assertEqual(refresh_stream_thumbnails.run(), "queued:2")
        queued = [call.kwargs["args"][1] for call in apply_async.call_args_list]
        # live/b has no thumbnail yet; live/d's is current.
        self.assertEqual(queued, ["live/b", "live/a"])
