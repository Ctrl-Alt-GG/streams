from collections.abc import Mapping
from typing import Literal

MediaKind = Literal["audio", "video", "unknown"]


def media_kind_from_tracks(tracks: object) -> MediaKind:
    if not isinstance(tracks, list) or not tracks:
        return "unknown"

    has_audio_metadata = False
    video_codecs = {
        "h264",
        "h265",
        "hevc",
        "av1",
        "vp8",
        "vp9",
        "mpeg4video",
        "mpeg1video",
        "mpeg2video",
        "mjpeg",
    }
    audio_codecs = {
        "aac",
        "mpeg4audio",
        "opus",
        "vorbis",
        "g711",
        "g722",
        "g726",
        "lpcm",
        "mp3",
        "mpeg1audio",
        "ac3",
        "pcma",
        "pcmu",
    }
    for track in tracks:
        if not isinstance(track, Mapping):
            continue
        codec = str(track.get("codec", "")).casefold().replace("-", "")
        if codec in video_codecs:
            return "video"
        if track.get("width") is not None or track.get("height") is not None:
            return "video"
        if (
            codec in audio_codecs
            or track.get("sample_rate") is not None
            or track.get("channel_count") is not None
        ):
            has_audio_metadata = True
    return "audio" if has_audio_metadata else "unknown"
