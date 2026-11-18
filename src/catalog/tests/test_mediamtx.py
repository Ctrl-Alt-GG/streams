import httpx
from django.test import SimpleTestCase, override_settings
from pydantic import ValidationError as PydanticValidationError

from catalog.services.mediamtx import ActivePath, MediaMTXClient, MediaMTXSchemaError
from catalog.services.observations import MediaTrack
from catalog.services.reconciliation import failure_reason


def media_server(pages, *, status_code=200, on_request=None):
    """An HTTP client for a media server that answers with `pages` of items."""
    requests = []

    def answer(request):
        requests.append(request)
        if on_request is not None:
            on_request(request)
        page = int(request.url.params["page"])
        return httpx.Response(
            status_code,
            json={
                "itemCount": sum(len(items) for items in pages),
                "pageCount": len(pages),
                "items": pages[page] if page < len(pages) else [],
            },
        )

    client = httpx.Client(base_url="https://media.example/", transport=httpx.MockTransport(answer))
    return client, requests


class TrackTests(SimpleTestCase):
    def test_mediamtx_reports_track_properties_in_codec_props(self):
        track = MediaTrack.model_validate(
            {"codec": "Opus", "codecProps": {"sampleRate": 48000, "channelCount": 2}}
        )
        self.assertEqual((track.codec, track.sample_rate, track.channel_count), ("Opus", 48000, 2))
        video = MediaTrack.model_validate(
            {"codec": "H264", "codecProps": {"width": 1280, "height": 720}}
        )
        self.assertEqual((video.width, video.height), (1280, 720))

    def test_a_property_in_a_form_the_catalog_cant_use_is_unknown(self):
        for value in ["broken", "360", -1, 0, 10**9, 3.5, True, [360], {"pixels": 360}]:
            with self.subTest(width=value):
                track = MediaTrack.model_validate(
                    {"codec": "H264", "codecProps": {"width": value, "height": 360}}
                )
                self.assertIsNone(track.width)
                self.assertEqual(track.height, 360)

    def test_a_track_that_isnt_an_object_is_left_out(self):
        path = ActivePath.model_validate(
            {"name": "live/x", "available": True, "tracks2": ["broken", None, {"codec": "VP9"}]}
        )
        self.assertEqual(path.tracks2, [MediaTrack(codec="VP9")])

    def test_old_mediamtx_schema_is_reported_instead_of_showing_every_stream_offline(self):
        with self.assertRaises(PydanticValidationError):
            ActivePath.model_validate({"name": "live/old", "ready": True})
        client, _ = media_server([[{"name": "old", "ready": True}]])
        with client, self.assertRaises(MediaMTXSchemaError):
            MediaMTXClient(client).list_active_paths()


class PagingTests(SimpleTestCase):
    def test_every_page_is_read(self):
        pages = [
            [{"name": f"live/{index}", "available": True} for index in range(100)],
            [{"name": "live/last", "available": False}],
        ]
        client, requests = media_server(pages)
        with client:
            paths = MediaMTXClient(client).list_active_paths()
        self.assertEqual(len(paths), 101)
        self.assertEqual([request.url.params["page"] for request in requests], ["0", "1"])

    def test_refused_and_failed_answers_say_why(self):
        for status_code, reason in [(401, "auth"), (403, "auth"), (503, "server")]:
            client, _ = media_server([[]], status_code=status_code)
            with self.subTest(status_code=status_code), client:
                with self.assertRaises(httpx.HTTPStatusError) as raised:
                    MediaMTXClient(client).list_active_paths()
                self.assertEqual(failure_reason(raised.exception), reason)

    @override_settings(MEDIAMTX_API_READ_TIMEOUT=5.0)
    def test_a_check_ends_at_its_deadline_however_many_pages_are_left(self):
        now = [0.0]

        def slow(_request):
            now[0] += 3

        pages = [[{"name": f"live/{page}", "available": True}] for page in range(3)]
        client, requests = media_server(pages, on_request=slow)
        with client, self.assertRaises(httpx.TimeoutException) as raised:
            MediaMTXClient(client, clock=lambda: now[0]).list_active_paths(deadline=5)
        # The first page is asked for with 5 seconds left, the second with the 2 that are left
        # then, and the third not at all.
        self.assertEqual([request.url.params["page"] for request in requests], ["0", "1"])
        timeouts = [request.extensions["timeout"]["read"] for request in requests]
        self.assertEqual(timeouts, [5.0, 2.0])
        self.assertEqual(failure_reason(raised.exception), "timeout")
