import re
from collections.abc import Iterable
from typing import Literal

from catalog.services.observations import MediaTrack, PathObservation

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


def media_kind_from_tracks(tracks: Iterable[MediaTrack]) -> MediaKind:
    has_audio_metadata = False
    for track in tracks:
        codec = re.sub(r"[^a-z0-9]", "", track.codec.casefold())
        if codec in VIDEO_CODECS or track.width is not None or track.height is not None:
            return "video"
        if (
            codec in AUDIO_CODECS
            or track.sample_rate is not None
            or track.channel_count is not None
        ):
            has_audio_metadata = True
    return "audio" if has_audio_metadata else "unknown"


def effective_media_kind(recorded: str, path: PathObservation | None) -> MediaKind:
    """
    What a channel carries: what the media server reports now, or else what it reported last
    (`recorded`). An offline channel has no tracks, and an audio channel stays one meanwhile.
    """
    observed = media_kind_from_tracks(path.tracks) if path is not None else "unknown"
    if observed != "unknown":
        return observed
    return recorded if recorded in ("audio", "video") else "unknown"
