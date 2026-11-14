"""
Settings for the capture worker. It decodes video that any publisher controls, so it gets only
what capturing needs: Redis, the broker and the media server's HLS address. A decoder bug
exploited there finds no database and none of the site's secrets.
"""

from django.core.management.utils import get_random_secret_key

from config.settings.base import *  # noqa: F403
from config.settings.base import CACHES, env

ENVIRONMENT = "production"
DEBUG = False
# The worker signs nothing.
SECRET_KEY = get_random_secret_key()
# Django's placeholder backend, which fails any query.
DATABASES = {}
CACHES = {"default": {**CACHES["default"], "LOCATION": env("REDIS_URL")}}
CELERY_BROKER_URL = env("CELERY_BROKER_URL")
