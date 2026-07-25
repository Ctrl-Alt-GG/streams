from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
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


def _detail(stream_id) -> dict:
    source, stream = CatalogService().get(stream_id)
    return {"source": source.as_dict(), "result": stream.as_public_dict()}


class StreamListView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(operation_id="stream_list", responses=StreamListEnvelopeSerializer)
    def get(self, request):
        catalog = CatalogService().list()
        return Response(
            {
                "source": catalog.source.as_dict(),
                "results": [stream.as_public_dict() for stream in catalog.streams],
            }
        )


class StreamDetailView(APIView):
    queryset = Stream.objects.all()

    def get_permissions(self):
        return [DjangoModelPermissions()] if self.request.method == "PATCH" else [AllowAny()]

    @extend_schema(operation_id="stream_retrieve", responses=StreamDetailEnvelopeSerializer)
    def get(self, request, stream_id):
        get_object_or_404(Stream, pk=stream_id)
        return Response(_detail(stream_id))

    @extend_schema(
        operation_id="stream_update_overlay",
        request=StreamOverlaySerializer,
        responses=StreamDetailEnvelopeSerializer,
    )
    def patch(self, request, stream_id):
        stream = get_object_or_404(Stream, pk=stream_id)
        serializer = StreamOverlaySerializer(stream, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(_detail(stream_id))


class PublishingConfigurationView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="publishing_configuration_retrieve",
        responses=PublishingConfigurationSerializer,
    )
    def get(self, request):
        return Response(get_publishing_configuration().as_dict())
