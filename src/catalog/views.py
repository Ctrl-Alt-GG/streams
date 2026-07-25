from django.http import Http404
from django.shortcuts import render

from catalog.guides import get_publishing_configuration
from catalog.models import Stream
from catalog.services.catalog import CatalogService


def home(request):
    return render(request, "catalog/home.html", {"catalog": CatalogService().list()})


def stream_detail(request, stream_id):
    try:
        source, stream = CatalogService().get(stream_id)
    except Stream.DoesNotExist as error:
        raise Http404 from error
    return render(request, "catalog/stream_detail.html", {"source": source, "stream": stream})


def tutorial(request):
    return render(
        request,
        "catalog/tutorial.html",
        {"publishing": get_publishing_configuration()},
    )
