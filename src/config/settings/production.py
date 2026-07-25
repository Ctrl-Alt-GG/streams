from urllib.parse import urlsplit

from config.settings.base import *  # noqa: F403
from config.settings.base import env

ENVIRONMENT = "production"
DEBUG = False
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = [*env.list("DJANGO_ALLOWED_HOSTS"), "127.0.0.1"]
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS")
DATABASES = {"default": env.db("DATABASE_URL")}

REDIS_URL = env("REDIS_URL")
CACHES = {
    "default": {
        # Keep django-redis for its atomic lock API and JSON-only serialization.
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SERIALIZER": "django_redis.serializers.json.JSONSerializer",
            "SOCKET_CONNECT_TIMEOUT": 2,
            "SOCKET_TIMEOUT": 2,
        },
    }
}

MINIO_ENDPOINT_URL = env("MINIO_ENDPOINT_URL")
MINIO_STATIC_PUBLIC_URL = env("MINIO_STATIC_PUBLIC_URL")
MINIO_STATIC_BUCKET = env("MINIO_STATIC_BUCKET")
MINIO_STATIC_ACCESS_KEY = env("MINIO_STATIC_ACCESS_KEY")
MINIO_STATIC_SECRET_KEY = env("MINIO_STATIC_SECRET_KEY")
MINIO_REGION = env("MINIO_REGION", default="us-east-1")

public_static = urlsplit(MINIO_STATIC_PUBLIC_URL)
STATIC_URL = MINIO_STATIC_PUBLIC_URL
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "storages.backends.s3.S3ManifestStaticStorage",
        "OPTIONS": {
            "access_key": MINIO_STATIC_ACCESS_KEY,
            "secret_key": MINIO_STATIC_SECRET_KEY,
            "bucket_name": MINIO_STATIC_BUCKET,
            "endpoint_url": MINIO_ENDPOINT_URL,
            "region_name": MINIO_REGION,
            "addressing_style": "path",
            "querystring_auth": False,
            "file_overwrite": True,
            "custom_domain": f"{public_static.netloc}{public_static.path.rstrip('/')}",
            "url_protocol": f"{public_static.scheme}:",
            "object_parameters": {
                "CacheControl": "public,max-age=31536000,immutable",
            },
        },
    },
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=31_536_000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
