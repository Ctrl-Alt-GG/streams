"""
For the tests that need PostgreSQL and Redis as deployed: concurrent writes, locks between
processes and migrations of existing rows (`manage.py test catalog --tag integration`).
DATABASE_URL and REDIS_URL name a server the tests may create a database on, and a Redis
database they may empty.
"""

from config.settings import base
from config.settings.test import *  # noqa: F403

DATABASES = {"default": base.env.db("DATABASE_URL")}
CACHES = {"default": {**base.CACHES["default"], "LOCATION": base.env("REDIS_URL")}}
SILENCED_SYSTEM_CHECKS = []
