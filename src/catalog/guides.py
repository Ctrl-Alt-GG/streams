from dataclasses import asdict, dataclass

from django.conf import settings


@dataclass(frozen=True, slots=True)
class PublishingGuide:
    server_url: str
    authentication_required: bool
    obs_steps: tuple[str, ...]
    ffmpeg_template: str
    safety_notes: tuple[str, ...]

    def as_dict(self) -> dict:
        return asdict(self)


def get_publishing_guide() -> PublishingGuide:
    return PublishingGuide(
        server_url=settings.MEDIAMTX_RTMP_PUBLIC_BASE_URL,
        authentication_required=True,
        obs_steps=(
            "Open Settings, then select Stream.",
            "Choose Custom as the service and enter the server URL shown here.",
            "Enter the assigned stream path as the stream key.",
            "Enter publishing credentials when prompted, then start streaming.",
        ),
        ffmpeg_template=(
            'ffmpeg -re -i INPUT -c:v libx264 -c:a aac -f flv "RTMPS_SERVER/STREAM_PATH"'
        ),
        safety_notes=(
            "Publishing credentials are assigned separately and are never displayed here.",
            "Treat the stream path and credentials as private.",
            "Use the TLS-protected RTMPS endpoint only.",
        ),
    )
