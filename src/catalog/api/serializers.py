from collections.abc import Mapping

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from catalog.models import Stream


class TrackSerializer(serializers.Serializer):
    codec = serializers.CharField()
    width = serializers.IntegerField(allow_null=True)
    height = serializers.IntegerField(allow_null=True)
    sample_rate = serializers.IntegerField(allow_null=True)
    channel_count = serializers.IntegerField(allow_null=True)


class StreamSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    display_name = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    effective_name = serializers.CharField()
    status = serializers.ChoiceField(choices=("live", "offline", "unknown"))
    available = serializers.BooleanField(allow_null=True)
    online = serializers.BooleanField(allow_null=True)
    tracks = TrackSerializer(many=True)
    observed_at = serializers.CharField(allow_null=True)
    stale = serializers.BooleanField()
    watch_url = serializers.URLField()
    hls_url = serializers.URLField(allow_null=True)


class SourceSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("fresh", "stale", "unavailable"))
    observed_at = serializers.CharField(allow_null=True)
    age_seconds = serializers.FloatField(allow_null=True)
    failure_count = serializers.IntegerField(min_value=0)


class StreamListEnvelopeSerializer(serializers.Serializer):
    source = SourceSerializer()
    results = StreamSerializer(many=True)


class StreamDetailEnvelopeSerializer(serializers.Serializer):
    source = SourceSerializer()
    result = StreamSerializer()


class StreamOverlaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Stream
        fields = ("display_name", "description")

    def to_internal_value(self, data):
        allowed_fields = {"display_name", "description"}
        unknown = set(data) - allowed_fields if isinstance(data, Mapping) else set()
        if unknown:
            raise serializers.ValidationError(
                {key: _("Unexpected field.") for key in sorted(unknown)}
            )
        return super().to_internal_value(data)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(_("Provide at least one overlay field."))
        return attrs


class PublishingConfigurationSerializer(serializers.Serializer):
    server_url = serializers.CharField()
    authentication_required = serializers.BooleanField()
    stream_key_prefix = serializers.CharField()
    stream_key_example = serializers.CharField()
    ffmpeg_template = serializers.CharField()
