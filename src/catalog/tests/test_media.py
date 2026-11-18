from django.template.loader import render_to_string
from django.test import SimpleTestCase

from catalog.services.media import effective_media_kind, media_kind_from_tracks
from catalog.services.observations import MediaTrack, PathObservation
from catalog.services.playback import encode_path


def tracks(*items):
    return [MediaTrack.model_validate(item) for item in items]


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

    def test_server_error_template_does_not_need_request_or_static_storage(self):
        from unittest.mock import patch

        with patch(
            "django.contrib.staticfiles.storage.staticfiles_storage.url", side_effect=ValueError
        ):
            self.assertIn("Streams", render_to_string("500.html"))


class MediaKindTests(SimpleTestCase):
    def test_video_codec_is_sufficient_without_dimensions(self):
        self.assertEqual(
            media_kind_from_tracks(tracks({"codec": "H264"}, {"codec": "Opus"})), "video"
        )
        self.assertEqual(media_kind_from_tracks(tracks({"codec": "H265"})), "video")
        self.assertEqual(media_kind_from_tracks(tracks({"codec": "MPEG4Audio"})), "audio")
        self.assertEqual(media_kind_from_tracks(tracks({"codec": "not-a-known-codec"})), "unknown")
        self.assertEqual(media_kind_from_tracks([]), "unknown")

    def test_codec_names_as_mediamtx_reports_them_decide_the_media_kind(self):
        # Codec names, and the properties that come with them, from MediaMTX 1.21.3 for streams
        # published over RTMP, RTSP and SRT. Several come without dimensions or a sample rate.
        for items, media_kind in [
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
            with self.subTest(tracks=items):
                self.assertEqual(media_kind_from_tracks(tracks(*items)), media_kind)

    def test_a_channel_keeps_what_it_carries_while_it_is_offline(self):
        offline = PathObservation(available=False)
        live_audio = PathObservation(available=True, tracks=tuple(tracks({"codec": "Opus"})))
        self.assertEqual(effective_media_kind("audio", offline), "audio")
        self.assertEqual(effective_media_kind("audio", None), "audio")
        self.assertEqual(effective_media_kind("video", live_audio), "audio")
        self.assertEqual(effective_media_kind("unknown", None), "unknown")
