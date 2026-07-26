from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseNotModified
from django.shortcuts import redirect, render
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_safe

from catalog.guides import get_publishing_configuration
from catalog.models import Stream
from catalog.services.catalog import CatalogService
from catalog.services.thumbnails import audio_thumbnail_url, fallback_thumbnail_url, get_thumbnail


@require_safe
def home(request):
    return render(request, "catalog/home.html", {"catalog": CatalogService().list()})


@require_safe
def stream_detail(request, stream_id):
    try:
        source, stream = CatalogService().get(stream_id)
    except Stream.DoesNotExist as error:
        raise Http404 from error
    return render(request, "catalog/stream_detail.html", {"source": source, "stream": stream})


@require_safe
def stream_thumbnail(request, stream_id):
    thumbnail = get_thumbnail(stream_id)
    if thumbnail is None:
        stream = Stream.objects.filter(pk=stream_id).only("path_name", "media_kind").first()
        if stream is None:
            raise Http404
        thumbnail_url = (
            audio_thumbnail_url(stream.path_name)
            if stream.media_kind == Stream.MediaKind.AUDIO
            else fallback_thumbnail_url(stream.path_name)
        )
        response = redirect(thumbnail_url)
        patch_cache_control(response, public=True, max_age=5)
        return response

    content = thumbnail.content
    content_type = thumbnail.content_type
    etag = thumbnail.etag
    max_age = min(30, settings.STREAM_THUMBNAIL_REFRESH_SECONDS)

    quoted_etag = f'"{etag}"'
    if request.headers.get("If-None-Match") == quoted_etag:
        response = HttpResponseNotModified()
    else:
        response = HttpResponse(content, content_type=content_type)
    response.headers["ETag"] = quoted_etag
    response.headers["X-Content-Type-Options"] = "nosniff"
    patch_cache_control(response, public=True, max_age=max_age)
    return response


@require_safe
def tutorial(request):
    return render(
        request,
        "catalog/tutorial.html",
        {"publishing": get_publishing_configuration()},
    )
