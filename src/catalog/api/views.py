from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, DjangoModelPermissions
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.api.serializers import (
    PublishingConfigurationSerializer,
    StreamDetailEnvelopeSerializer,
    StreamListEnvelopeSerializer,
    StreamOverlaySerializer,
)
from catalog.guides import get_publishing_configuration
from catalog.models import Stream
from catalog.services.catalog import CatalogService


def _catalog_detail(stream_id):
    try:
        return CatalogService().get(stream_id)
    except Stream.DoesNotExist as error:
        raise Http404 from error


def _serialize_detail(source, stream) -> dict:
    return StreamDetailEnvelopeSerializer({"source": source, "result": stream}).data


def _detail(stream_id) -> dict:
    return _serialize_detail(*_catalog_detail(stream_id))


class StreamListView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(operation_id="stream_list", responses=StreamListEnvelopeSerializer)
    def get(self, request):
        catalog = CatalogService().list()
        return Response(
            StreamListEnvelopeSerializer(
                {"source": catalog.source, "results": catalog.streams}
            ).data
        )


class StreamDetailView(mixins.UpdateModelMixin, GenericAPIView):
    queryset = Stream.objects.all()
    serializer_class = StreamOverlaySerializer
    lookup_url_kwarg = "stream_id"

    def get_permissions(self):
        return [DjangoModelPermissions()] if self.request.method == "PATCH" else [AllowAny()]

    @extend_schema(operation_id="stream_retrieve", responses=StreamDetailEnvelopeSerializer)
    def get(self, request, stream_id):
        return Response(_detail(stream_id))

    @extend_schema(
        operation_id="stream_update_overlay",
        request=StreamOverlaySerializer,
        responses=StreamOverlaySerializer,
    )
    def patch(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)


class PublishingConfigurationView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="publishing_configuration_retrieve",
        responses=PublishingConfigurationSerializer,
    )
    def get(self, request):
        configuration = get_publishing_configuration()
        return Response(PublishingConfigurationSerializer(configuration).data)
