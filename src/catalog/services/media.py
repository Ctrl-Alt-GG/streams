import re
from collections.abc import Mapping
from typing import Literal

MediaKind = Literal["audio", "video", "unknown"]

# Codec names as MediaMTX reports them, without case, spaces and punctuation: it calls AAC
# "MPEG-4 Audio" and MP3 "MPEG-1/2 Audio". Some codecs come without dimensions or a sample rate,
# so the name alone has to tell.
VIDEO_CODECS = {
    "h264",
    "h265",
    "hevc",
    "av1",
    "vp8",
    "vp9",
    "mjpeg",
    "mpeg4video",
    "mpeg12video",
    "mpeg1video",
    "mpeg2video",
}
AUDIO_CODECS = {
    "mpeg4audio",
    "mpeg4audiolatm",
    "mpeg12audio",
    "aac",
    "mp3",
    "mpeg1audio",
    "opus",
    "vorbis",
    "ac3",
    "g711",
    "g722",
    "g726",
    "lpcm",
    "pcma",
    "pcmu",
}


def media_kind_from_tracks(tracks: object) -> MediaKind:
    if not isinstance(tracks, list) or not tracks:
        return "unknown"

    has_audio_metadata = False
    for track in tracks:
        if not isinstance(track, Mapping):
            continue
        codec = re.sub(r"[^a-z0-9]", "", str(track.get("codec", "")).casefold())
        if codec in VIDEO_CODECS:
            return "video"
        if track.get("width") is not None or track.get("height") is not None:
            return "video"
        if (
            codec in AUDIO_CODECS
            or track.get("sample_rate") is not None
            or track.get("channel_count") is not None
        ):
            has_audio_metadata = True
    return "audio" if has_audio_metadata else "unknown"
