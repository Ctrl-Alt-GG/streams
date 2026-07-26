from collections.abc import Mapping
from typing import Literal

MediaKind = Literal["audio", "video", "unknown"]


def media_kind_from_tracks(tracks: object) -> MediaKind:
    if not isinstance(tracks, list) or not tracks:
        return "unknown"

    has_audio_metadata = False
    for track in tracks:
        if not isinstance(track, Mapping):
            return "unknown"
        if track.get("width") is not None or track.get("height") is not None:
            return "video"
        if track.get("sample_rate") is not None or track.get("channel_count") is not None:
            has_audio_metadata = True
    return "audio" if has_audio_metadata else "unknown"
