# The images of the web app, the Celery workers and Celery beat (`--target web`, `worker` or
# `beat`). They share the Python runtime and the application, and differ in what they run.

FROM python:3.14-slim AS runtime

ENV DJANGO_SETTINGS_MODULE=config.settings.production \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && python -m pip install --no-cache-dir uv==0.11.32

COPY pyproject.toml uv.lock README.md ./
RUN uv export --frozen --no-dev --no-emit-project --output-file /tmp/requirements.txt \
    && uv pip install --system --require-hashes --no-cache \
        --python /usr/local/bin/python --requirement /tmp/requirements.txt \
    && python -m pip uninstall --yes uv \
    && rm -rf /root/.cache /tmp/requirements.txt

COPY manage.py ./
COPY src ./src


# The status worker, and the capture worker, which compose runs with its own settings and queue.
FROM runtime AS worker

LABEL org.opencontainers.image.description="Streams Celery worker: processes the status and thumbnail queues for the Ctrl-Alt-GG streams directory."

USER 10001:10001

CMD ["/bin/sh", "-c", "exec celery --app=config worker --loglevel=\"${CELERY_LOG_LEVEL:-INFO}\" --queues=status-quorum --concurrency=2 --hostname=\"worker@${HOSTNAME}\" --without-mingle --without-gossip"]


FROM runtime AS beat

LABEL org.opencontainers.image.description="Streams Celery beat: schedules periodic tasks for the Ctrl-Alt-GG streams directory."

USER 10001:10001

CMD ["/bin/sh", "-c", "exec celery --app=config beat --loglevel=\"${CELERY_LOG_LEVEL:-INFO}\" --pidfile=/tmp/celerybeat.pid --schedule=/tmp/celerybeat-schedule"]


# Last, so that a build without --target builds the web app.
FROM runtime AS web

LABEL org.opencontainers.image.description="Streams web: network-private Django directory for streams served by an external MediaMTX deployment."

# gettext compiles the translations below.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*

COPY generated_static ./generated_static

RUN python manage.py compilemessages -l hu --settings=config.settings.base \
    && python manage.py tailwind --settings=config.settings.base build --force \
    && python manage.py collectstatic --noinput --settings=config.settings.base \
    && rm -rf .django_tailwind_cli

USER 10001:10001
EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--config", "python:config.gunicorn"]
