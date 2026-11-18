from django.test import SimpleTestCase

from catalog.checks import check_streams_settings


class SettingsCheckTests(SimpleTestCase):
    def errors(self, **settings):
        with self.settings(**settings):
            return [(error.id, error.msg) for error in check_streams_settings()]

    def assert_refused(self, check_id, setting, **settings):
        errors = self.errors(**settings)
        self.assertTrue(
            any(error_id == check_id and setting in message for error_id, message in errors),
            errors,
        )

    def test_the_default_settings_pass(self):
        self.assertEqual(check_streams_settings(), [])

    def test_addresses_need_a_supported_scheme_and_a_host(self):
        for name, value in [
            ("PUBLIC_BASE_URL", "streams.example.com"),
            ("MEDIAMTX_API_BASE_URL", "ftp://media.example"),
            ("MEDIAMTX_HLS_PUBLIC_BASE_URL", "/hls/"),
            ("MEDIAMTX_HLS_CAPTURE_BASE_URL", "https://"),
            ("MEDIAMTX_RTMP_PUBLIC_BASE_URL", "https://streams.example.com"),
        ]:
            with self.subTest(name=name):
                self.assert_refused("streams.E001", name, **{name: value})

    def test_durations_and_counts_have_to_be_positive(self):
        for name in ["MEDIAMTX_API_READ_TIMEOUT", "STREAM_THUMBNAIL_REFRESH_SECONDS"]:
            for value in [0, -1]:
                with self.subTest(name=name, value=value):
                    self.assert_refused("streams.E002", name, **{name: value})

    def test_negative_retention_would_hide_every_channel_that_isnt_live(self):
        self.assert_refused(
            "streams.E003", "STREAM_HIDE_OFFLINE_AFTER_DAYS", STREAM_HIDE_OFFLINE_AFTER_DAYS=-1
        )
        self.assertEqual(self.errors(STREAM_HIDE_OFFLINE_AFTER_DAYS=0), [])

    def test_locks_outlast_the_tasks_and_checks_end_before_celery_stops_them(self):
        self.assert_refused(
            "streams.E004", "STREAM_THUMBNAIL_LOCK_SECONDS", STREAM_THUMBNAIL_LOCK_SECONDS=8
        )
        self.assert_refused(
            "streams.E004",
            "MEDIAMTX_RECONCILE_BUDGET_SECONDS",
            MEDIAMTX_RECONCILE_BUDGET_SECONDS=7,
        )
        self.assert_refused(
            "streams.E004",
            "MEDIAMTX_FRESHNESS_SECONDS",
            MEDIAMTX_FRESHNESS_SECONDS=5,
        )
        self.assert_refused(
            "streams.E004",
            "STREAM_THUMBNAIL_CACHE_SECONDS",
            STREAM_THUMBNAIL_CACHE_SECONDS=30,
        )
