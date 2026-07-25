from dataclasses import asdict, dataclass

from django.conf import settings


@dataclass(frozen=True, slots=True)
class PublishingConfiguration:
    server_url: str
    authentication_required: bool
    stream_key_prefix: str
    stream_key_example: str
    ffmpeg_template: str

    def as_dict(self) -> dict:
        return asdict(self)


def get_publishing_configuration() -> PublishingConfiguration:
    server_url = settings.MEDIAMTX_RTMP_PUBLIC_BASE_URL.rstrip("/")
    stream_key_prefix = "live/"
    stream_key_example = f"{stream_key_prefix}SOMETHING"
    return PublishingConfiguration(
        server_url=server_url,
        authentication_required=True,
        stream_key_prefix=stream_key_prefix,
        stream_key_example=stream_key_example,
        ffmpeg_template=(
            f'ffmpeg -re -i INPUT -c:v libx264 -c:a aac -f flv "{server_url}/{stream_key_example}"'
        ),
    )
