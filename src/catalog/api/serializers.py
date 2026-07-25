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
    effective_name = serializers.CharField()
    status = serializers.ChoiceField(choices=("live", "offline", "unknown"))
    available = serializers.BooleanField(allow_null=True)
    online = serializers.BooleanField(allow_null=True)
    tracks = TrackSerializer(many=True)
    observed_at = serializers.DateTimeField(allow_null=True)
    stale = serializers.BooleanField()
    watch_url = serializers.URLField()
    hls_url = serializers.URLField(allow_null=True)


class SourceSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("fresh", "stale", "unavailable"))
    observed_at = serializers.DateTimeField(allow_null=True)
    age_seconds = serializers.FloatField(allow_null=True)
    failure_count = serializers.IntegerField(min_value=0)


class StreamListEnvelopeSerializer(serializers.Serializer):
    source = SourceSerializer()
    results = StreamSerializer(many=True)


class StreamDetailEnvelopeSerializer(serializers.Serializer):
    source = SourceSerializer()
    result = StreamSerializer()


class DisplayNameSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=200, allow_blank=True, trim_whitespace=True)

    def to_internal_value(self, data):
        unknown = set(data) - {"display_name"} if isinstance(data, dict) else set()
        if unknown:
            raise serializers.ValidationError({key: "Unexpected field." for key in sorted(unknown)})
        return super().to_internal_value(data)

    def update(self, instance: Stream, validated_data):
        instance.display_name = validated_data["display_name"]
        instance.save(update_fields=("display_name", "updated_at"))
        return instance


class PublishingGuideSerializer(serializers.Serializer):
    server_url = serializers.CharField()
    authentication_required = serializers.BooleanField()
    obs_steps = serializers.ListField(child=serializers.CharField())
    ffmpeg_template = serializers.CharField()
    safety_notes = serializers.ListField(child=serializers.CharField())
