from urllib.parse import quote

from django.conf import settings


def encode_path(path_name: str) -> str:
    segments = path_name.split("/")
    if (
        not path_name
        or path_name.startswith("/")
        or path_name.endswith("/")
        or any(segment in {".", ".."} for segment in segments)
    ):
        raise ValueError("Stream path contains an unsafe segment.")
    return "/".join(quote(segment, safe="") for segment in segments)


def build_hls_embed_url(path_name: str) -> str:
    base_url = settings.MEDIAMTX_HLS_PUBLIC_BASE_URL.rstrip("/")
    return f"{base_url}/{encode_path(path_name)}"


def build_hls_url(path_name: str) -> str:
    return f"{build_hls_embed_url(path_name)}/index.m3u8"


def build_hls_capture_url(path_name: str) -> str:
    base_url = settings.MEDIAMTX_HLS_CAPTURE_BASE_URL.rstrip("/")
    return f"{base_url}/{encode_path(path_name)}/index.m3u8"
