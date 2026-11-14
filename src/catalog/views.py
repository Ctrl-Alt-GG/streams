from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.utils.http import quote_etag
from django.views.decorators.http import conditional_page, require_safe

from catalog.guides import get_publishing_configuration
from catalog.models import Stream
from catalog.services.catalog import CatalogService
from catalog.services.thumbnails import audio_thumbnail_url, fallback_thumbnail_url, get_thumbnail


def _watch_state(url, streams):
    """What the page shows, and where catalog.js checks whether that is still current."""
    return {
        "url": url,
        "streams": [
            [stream.id, stream.status, stream.audio_only, stream.effective_name, stream.description]
            for stream in streams
        ],
    }


@require_safe
def home(request):
    catalog = CatalogService().list(public_urls=False)
    return render(
        request,
        "catalog/home.html",
        {
            "catalog": catalog,
            "watch_state": _watch_state(reverse("catalog-api:stream-list"), catalog.streams),
        },
    )


@require_safe
def stream_detail(request, stream_id):
    try:
        source, stream = CatalogService().get(stream_id, public_urls=False)
    except Stream.DoesNotExist as error:
        raise Http404 from error
    return render(
        request,
        "catalog/stream_detail.html",
        {
            "source": source,
            "stream": stream,
            "watch_state": _watch_state(
                reverse("catalog-api:stream-detail", args=[stream.id]), [stream]
            ),
        },
    )


@require_safe
@conditional_page
def stream_thumbnail(request, stream_id):
    thumbnail = get_thumbnail(stream_id)
    if thumbnail is None:
        stream = Stream.objects.filter(pk=stream_id).only("path_name", "media_kind").first()
        if stream is None:
            raise Http404
        thumbnail_url = (
            audio_thumbnail_url(stream.path_name, public=False)
            if stream.media_kind == Stream.MediaKind.AUDIO
            else fallback_thumbnail_url(stream.path_name, public=False)
        )
        response = redirect(thumbnail_url)
        patch_cache_control(response, public=True, max_age=5)
        return response

    response = HttpResponse(thumbnail.content, content_type=thumbnail.content_type)
    response.headers["ETag"] = quote_etag(thumbnail.etag)
    response.headers["X-Content-Type-Options"] = "nosniff"
    patch_cache_control(
        response, public=True, max_age=min(30, settings.STREAM_THUMBNAIL_REFRESH_SECONDS)
    )
    return response


@require_safe
def tutorial(request):
    return render(
        request,
        "catalog/tutorial.html",
        {"publishing": get_publishing_configuration()},
    )
