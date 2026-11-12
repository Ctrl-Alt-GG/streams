from django.core.exceptions import ImproperlyConfigured

from config.settings.production import *  # noqa: F403
from config.settings.production import ALLOWED_HOSTS

if any(host not in {"localhost", "127.0.0.1", "[::1]"} for host in ALLOWED_HOSTS):
    raise ImproperlyConfigured("Local debug settings require loopback-only DJANGO_ALLOWED_HOSTS.")

ENVIRONMENT = "local"
DEBUG = True

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
