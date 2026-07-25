import os

from celery import Celery
from celery.schedules import schedule
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

app = Celery("streams")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "refresh-mediamtx-snapshot": {
        "task": "catalog.tasks.refresh_mediamtx_snapshot",
        "schedule": schedule(run_every=settings.MEDIAMTX_RECONCILE_INTERVAL_SECONDS),
        "options": {"expires": 9.0, "queue": "mediamtx"},
    }
}
