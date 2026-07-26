import base64
import functools
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin
from uuid import uuid4

import av
from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.cache import cache
from PIL import Image, ImageOps

THUMBNAIL_SCHEMA_VERSION = 4
THUMBNAIL_SIZE = (640, 360)
THUMBNAIL_ASSET_MANIFEST = "catalog/thumbnails/manifest.json"
THUMBNAIL_CONTENT_TYPE = "image/webp"

ThumbnailKind = Literal["frame", "fallback", "offline", "audio"]


@dataclass(frozen=True, slots=True)
class CachedThumbnail:
    content: bytes
    content_type: str
    etag: str
    generated_at: str


@dataclass(frozen=True, slots=True)
class ThumbnailAssetManifest:
    fallbacks: tuple[str, ...]
    audio_fallbacks: tuple[str, ...]
    offline: str


def _cache_key(stream_id: object) -> str:
    return f"catalog:thumbnail:v{THUMBNAIL_SCHEMA_VERSION}:{stream_id}"


def _lock_key(stream_id: object) -> str:
    return f"catalog:thumbnail-lock:v{THUMBNAIL_SCHEMA_VERSION}:{stream_id}"


def get_thumbnail(stream_id: object) -> CachedThumbnail | None:
    payload = cache.get(_cache_key(stream_id))
    if not isinstance(payload, dict):
        return None
    if (
        payload.get("schema_version") != THUMBNAIL_SCHEMA_VERSION
        or payload.get("content_type") != THUMBNAIL_CONTENT_TYPE
        or not isinstance(payload.get("content"), str)
        or not isinstance(payload.get("etag"), str)
        or not isinstance(payload.get("generated_at"), str)
    ):
        return None
    try:
        content = base64.b64decode(payload["content"], validate=True)
    except ValueError:
        return None
    if not content:
        return None
    return CachedThumbnail(
        content=content,
        content_type=payload["content_type"],
        etag=payload["etag"],
        generated_at=payload["generated_at"],
    )


def write_thumbnail(
    stream_id: object,
    content: bytes,
) -> CachedThumbnail:
    generated_at = datetime.now(UTC).isoformat()
    etag = sha256(content).hexdigest()
    payload = {
        "schema_version": THUMBNAIL_SCHEMA_VERSION,
        "content": base64.b64encode(content).decode("ascii"),
        "content_type": THUMBNAIL_CONTENT_TYPE,
        "etag": etag,
        "generated_at": generated_at,
    }
    cache.set(
        _cache_key(stream_id),
        payload,
        timeout=settings.STREAM_THUMBNAIL_CACHE_SECONDS,
    )
    return CachedThumbnail(
        content=content,
        content_type=THUMBNAIL_CONTENT_TYPE,
        etag=etag,
        generated_at=generated_at,
    )


def delete_thumbnail(stream_id: object) -> None:
    cache.delete(_cache_key(stream_id))


def thumbnail_needs_refresh(stream_id: object) -> bool:
    thumbnail = get_thumbnail(stream_id)
    if thumbnail is None:
        return True
    try:
        generated_at = datetime.fromisoformat(thumbnail.generated_at)
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
    except ValueError:
        return True
    age = (datetime.now(UTC) - generated_at).total_seconds()
    return age >= settings.STREAM_THUMBNAIL_REFRESH_SECONDS


@contextmanager
def thumbnail_lock(stream_id: object) -> Iterator[bool]:
    key = _lock_key(stream_id)
    timeout = settings.STREAM_THUMBNAIL_LOCK_SECONDS
    lock_factory = getattr(cache, "lock", None)
    if lock_factory is not None:
        lock = lock_factory(key, timeout=timeout, blocking_timeout=0)
        acquired = lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()
        return

    token = uuid4().hex
    acquired = cache.add(key, token, timeout=timeout)
    try:
        yield acquired
    finally:
        if acquired and cache.get(key) == token:
            cache.delete(key)


def capture_thumbnail(hls_url: str) -> bytes:
    timeout = settings.STREAM_THUMBNAIL_CAPTURE_TIMEOUT_SECONDS
    options = {"rw_timeout": str(round(timeout * 1_000_000))}
    with av.open(hls_url, options=options, timeout=(timeout, timeout)) as container:
        if not container.streams.video:
            raise ValueError("The stream does not contain a video track.")
        video_stream = container.streams.video[0]
        video_stream.thread_type = "AUTO"
        for frame in container.decode(video_stream):
            image = ImageOps.fit(
                frame.to_image().convert("RGB"),
                THUMBNAIL_SIZE,
                method=Image.Resampling.LANCZOS,
            )
            return _encode_webp(image)
    raise ValueError("The stream ended before a video frame was decoded.")


def fallback_thumbnail_url(seed: str, *, variant: int | None = None) -> str:
    manifest = _thumbnail_assets()
    return _thumbnail_variant_url(seed, manifest.fallbacks, variant)


def audio_thumbnail_url(seed: str, *, variant: int | None = None) -> str:
    manifest = _thumbnail_assets()
    return _thumbnail_variant_url(seed, manifest.audio_fallbacks, variant)


def _thumbnail_variant_url(
    seed: str,
    assets: tuple[str, ...],
    variant: int | None,
) -> str:
    digest = sha256(seed.encode()).digest()
    asset_index = digest[0] % len(assets) if variant is None else variant % len(assets)
    return _static_asset_url(assets[asset_index])


def offline_thumbnail_url() -> str:
    manifest = _thumbnail_assets()
    return _static_asset_url(manifest.offline)


@functools.lru_cache(maxsize=1)
def _thumbnail_assets() -> ThumbnailAssetManifest:
    try:
        manifest = json.loads(_read_static_asset(THUMBNAIL_ASSET_MANIFEST))
    except json.JSONDecodeError as error:
        raise ValueError("Thumbnail asset manifest is not valid JSON.") from error
    return _parse_thumbnail_asset_manifest(manifest)


def _parse_thumbnail_asset_manifest(manifest: object) -> ThumbnailAssetManifest:
    if not isinstance(manifest, dict):
        raise ValueError("Thumbnail asset manifest must be an object.")

    fallback_names = _manifest_string_list(manifest, "fallbacks")
    audio_fallback_names = _manifest_string_list(manifest, "audio_fallbacks")
    offline = _manifest_string(manifest.get("offline"), "offline")
    return ThumbnailAssetManifest(
        fallbacks=fallback_names,
        audio_fallbacks=audio_fallback_names,
        offline=offline,
    )


def _manifest_string_list(manifest: dict, field: str) -> tuple[str, ...]:
    values = manifest.get(field)
    if not isinstance(values, list) or not values:
        raise ValueError(f"Thumbnail asset manifest {field} must be a non-empty array.")
    return tuple(_manifest_string(value, f"{field}[{index}]") for index, value in enumerate(values))


def _manifest_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Thumbnail asset manifest field '{field}' must be a non-empty string.")
    return value


def _static_asset_url(asset_name: str) -> str:
    asset_url = staticfiles_storage.url(asset_name)
    return urljoin(f"{settings.PUBLIC_BASE_URL.rstrip('/')}/", asset_url)


@functools.cache
def _read_static_asset(asset_name: str) -> bytes:
    asset_path = finders.find(asset_name)
    if not isinstance(asset_path, str):
        raise FileNotFoundError(f"Static thumbnail asset not found: {asset_name}")
    content = Path(asset_path).read_bytes()
    if not content:
        raise ValueError(f"Static thumbnail asset is empty: {asset_name}")
    return content


def _encode_webp(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="WEBP", quality=82, method=4)
    return output.getvalue()
