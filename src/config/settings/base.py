from pathlib import Path
from urllib.parse import urlsplit

import environ
from django.utils.csp import CSP

BASE_DIR = Path(__file__).resolve().parents[3]

env = environ.FileAwareEnv()
if (env_file := BASE_DIR / ".env").exists():
    environ.Env.read_env(env_file)

ENVIRONMENT = env("ENVIRONMENT", default="development")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
SECRET_KEY = env("DJANGO_SECRET_KEY", default="development-key-not-for-production")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default="http://localhost:8000")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "django_tailwind_cli",
    "storages",
    "health_check",
    "catalog.apps.CatalogConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

DATABASES = {"default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "streams-development",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = env("MINIO_STATIC_PUBLIC_URL", default="/static/")
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "generated_static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
}

TAILWIND_CLI_VERSION = "4.1.14"
TAILWIND_CLI_SRC_CSS = "src/catalog/static_src/app.css"
TAILWIND_CLI_DIST_CSS = "catalog/css/app.css"

MEDIAMTX_API_BASE_URL = env("MEDIAMTX_API_BASE_URL", default="https://mediamtx.invalid")
MEDIAMTX_API_USERNAME = env("MEDIAMTX_API_USERNAME", default="")
MEDIAMTX_API_PASSWORD = env("MEDIAMTX_API_PASSWORD", default="")
MEDIAMTX_API_VERIFY_TLS = env.bool("MEDIAMTX_API_VERIFY_TLS", default=True)
MEDIAMTX_API_CA_BUNDLE = env("MEDIAMTX_API_CA_BUNDLE", default="")
MEDIAMTX_API_CONNECT_TIMEOUT = env.float("MEDIAMTX_API_CONNECT_TIMEOUT", default=2.0)
MEDIAMTX_API_READ_TIMEOUT = env.float("MEDIAMTX_API_READ_TIMEOUT", default=5.0)
MEDIAMTX_HLS_PUBLIC_BASE_URL = env(
    "MEDIAMTX_HLS_PUBLIC_BASE_URL", default="https://streams.invalid/hls/"
)
MEDIAMTX_RTMP_PUBLIC_BASE_URL = env(
    "MEDIAMTX_RTMP_PUBLIC_BASE_URL", default="rtmps://streams.invalid/live"
)
MEDIAMTX_RECONCILE_INTERVAL_SECONDS = 10
MEDIAMTX_CACHE_TTL_SECONDS = 15

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.DjangoModelPermissionsOrAnonReadOnly"
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Streams API",
    "DESCRIPTION": "Read-only MediaMTX catalog with staff-managed display names.",
    "VERSION": "1.0.0",
    "OAS_VERSION": "3.1.0",
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAdminUser"],
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
}


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""


static_origin = _origin(STATIC_URL)
hls_origin = _origin(MEDIAMTX_HLS_PUBLIC_BASE_URL)
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "base-uri": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.SELF],
    "frame-src": [CSP.SELF, *([hls_origin] if hls_origin else [])],
    "object-src": [CSP.NONE],
    "script-src": [CSP.SELF, *([static_origin] if static_origin else [])],
    "style-src": [CSP.SELF, *([static_origin] if static_origin else [])],
    "font-src": [CSP.SELF, *([static_origin] if static_origin else [])],
    "img-src": [CSP.SELF, "data:", *([static_origin] if static_origin else [])],
    "connect-src": [CSP.SELF, *([hls_origin] if hls_origin else [])],
    "media-src": [CSP.SELF, "blob:", *([hls_origin] if hls_origin else [])],
    "worker-src": [CSP.SELF, "blob:"],
}
SECURE_CSP_REPORT_ONLY = {}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} level={levelname} logger={name} message={message}",
            "style": "{",
        }
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "verbose"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="amqp://guest:guest@localhost:5672//")
CELERY_RESULT_BACKEND = None
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_ROUTES = {"catalog.tasks.refresh_mediamtx_snapshot": {"queue": "mediamtx"}}
CELERY_TASK_SOFT_TIME_LIMIT = 7
CELERY_TASK_TIME_LIMIT = 9
