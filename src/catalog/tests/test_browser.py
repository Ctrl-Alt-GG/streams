"""
The player and the status checks in a browser, with streams that the tests encode themselves: a
video that plays, audio that starts when asked, playback that recovers after the stream failed,
and a page that comes back from the back/forward cache. They need Playwright's Chromium
(`uv run playwright install chromium`) and STREAMS_BROWSER_TESTS=1, and run with `--tag browser`.
"""

import functools
import os
import shutil
import tempfile
import threading
from fractions import Fraction
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import skipUnless

import av
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.test import override_settings, tag
from django.urls import reverse

from catalog.models import Stream
from catalog.services.observations import OBSERVATION_KEY
from catalog.tests.support import observe
from config.settings.base import content_security_policy

BROWSER_TESTS = os.environ.get("STREAMS_BROWSER_TESTS") == "1"
FRAMES_PER_SECOND = 25
SAMPLE_RATE = 48_000
SAMPLES_PER_FRAME = 960
SECONDS = 8
WAIT_MS = 20_000


def encode_hls(directory: Path, *, video: bool) -> None:
    """
    A short HLS stream in fMP4 segments, as MediaMTX serves them. VP9 and Opus, which the
    open-source Chromium of the tests decodes, stand in for H.264 and AAC.
    """
    directory.mkdir(parents=True)
    options = {
        "hls_time": "1",
        "hls_list_size": "0",
        "hls_playlist_type": "vod",
        "hls_segment_type": "fmp4",
    }
    with av.open(str(directory / "index.m3u8"), "w", format="hls", options=options) as container:
        picture = None
        if video:
            picture = container.add_stream(
                "libvpx-vp9",
                rate=FRAMES_PER_SECOND,
                options={"deadline": "realtime", "cpu-used": "8", "lag-in-frames": "0"},
            )
            picture.width, picture.height, picture.pix_fmt = 320, 180, "yuv420p"
            picture.time_base = Fraction(1, FRAMES_PER_SECOND)
        sound = container.add_stream("libopus", rate=SAMPLE_RATE, layout="stereo")
        sound.time_base = Fraction(1, SAMPLE_RATE)
        for second in range(SECONDS):
            if picture is not None:
                for index in range(FRAMES_PER_SECOND):
                    frame = av.VideoFrame(320, 180, "yuv420p")
                    number = second * FRAMES_PER_SECOND + index
                    for plane, value in zip(
                        frame.planes, (number * 3 % 256, 128, 128), strict=True
                    ):
                        plane.update(bytes([value]) * plane.buffer_size)
                    frame.pts, frame.time_base = number, Fraction(1, FRAMES_PER_SECOND)
                    container.mux(picture.encode(frame))
            for index in range(SAMPLE_RATE // SAMPLES_PER_FRAME):
                frame = av.AudioFrame(format="s16", layout="stereo", samples=SAMPLES_PER_FRAME)
                frame.planes[0].update(bytes(frame.planes[0].buffer_size))
                frame.sample_rate = SAMPLE_RATE
                frame.pts = (second * SAMPLE_RATE // SAMPLES_PER_FRAME + index) * SAMPLES_PER_FRAME
                frame.time_base = Fraction(1, SAMPLE_RATE)
                container.mux(sound.encode(frame))
        if picture is not None:
            container.mux(picture.encode(None))
        container.mux(sound.encode(None))


class MediaServer(ThreadingHTTPServer):
    """Serves the encoded streams as the media server's HLS address, which another origin is."""

    failing = False
    playlist_requests = 0


class MediaHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0].endswith("/index.m3u8"):
            self.server.playlist_requests += 1
            if self.server.failing:
                self.send_error(404)
                return
        super().do_GET()

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, format, *args):
        pass


@tag("browser")
@skipUnless(BROWSER_TESTS, "needs STREAMS_BROWSER_TESTS=1 and Playwright's Chromium")
class PlayerBrowserTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        # Playwright runs an event loop in this thread, where Django otherwise refuses queries.
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.media_root = Path(tempfile.mkdtemp())
        encode_hls(cls.media_root / "hls" / "live" / "video", video=True)
        encode_hls(cls.media_root / "hls" / "live" / "radio", video=False)
        cls.media = MediaServer(
            ("127.0.0.1", 0), functools.partial(MediaHandler, directory=str(cls.media_root))
        )
        threading.Thread(target=cls.media.serve_forever, daemon=True).start()
        hls_url = f"http://127.0.0.1:{cls.media.server_port}/hls/"
        cls.media_settings = override_settings(
            MEDIAMTX_HLS_PUBLIC_BASE_URL=hls_url,
            SECURE_CSP=content_security_policy(settings.STATIC_URL, hls_url),
        )
        cls.media_settings.enable()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.media_settings.disable()
        cls.media.shutdown()
        cls.media.server_close()
        shutil.rmtree(cls.media_root)
        super().tearDownClass()
        del os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"]

    def setUp(self):
        cache.clear()
        self.media.failing = False
        self.media.playlist_requests = 0
        self.video = Stream.objects.create(path_name="live/video")
        self.radio = Stream.objects.create(path_name="live/radio", media_kind="audio")
        observe(
            {
                "live/video": {"available": True, "tracks": [{"codec": "VP9"}, {"codec": "Opus"}]},
                "live/radio": {"available": True, "tracks": [{"codec": "Opus"}]},
            }
        )
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.set_default_timeout(WAIT_MS)

    def tearDown(self):
        self.context.close()

    def open(self, stream: Stream) -> None:
        self.page.goto(self.live_server_url + reverse("catalog:stream-detail", args=[stream.id]))

    def wait_until_playing(self) -> None:
        self.page.wait_for_function(
            """() => {
                const media = document.querySelector('[data-hls-player]');
                return !media.paused && media.currentTime > 0.5;
            }"""
        )

    def test_a_live_video_plays_by_itself(self):
        self.open(self.video)
        self.wait_until_playing()
        self.assertEqual(self.page.locator("video").evaluate("video => video.videoWidth"), 320)

    def test_audio_plays_once_the_visitor_starts_it(self):
        self.open(self.radio)
        player = self.page.locator("audio[data-hls-player]")
        self.assertTrue(player.evaluate("audio => audio.paused"))
        self.page.locator("[data-audio-start]").click()
        self.wait_until_playing()

    def test_playback_recovers_when_the_stream_is_back_without_reloading_the_page(self):
        self.media.failing = True
        self.open(self.radio)
        self.page.evaluate("() => { window.loadedOnce = true; }")
        self.page.locator("[data-audio-start]").click()
        self.page.locator("[data-player-message]").wait_for(state="visible")
        # The status source is gone too, as during a Redis restart: an unknown status is no
        # reason to give up the audio the visitor started.
        cache.delete(OBSERVATION_KEY)
        self.media.failing = False
        self.wait_until_playing()
        self.assertTrue(self.page.evaluate("() => window.loadedOnce === true"))
        self.assertGreaterEqual(self.media.playlist_requests, 2)

    def test_playback_resumes_after_going_back_to_the_page(self):
        self.open(self.video)
        self.wait_until_playing()
        self.page.locator(f'a[href="{reverse("catalog:home")}"]').first.click()
        self.page.wait_for_url(self.live_server_url + reverse("catalog:home"))
        self.page.go_back()
        self.wait_until_playing()
