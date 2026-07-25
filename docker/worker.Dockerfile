FROM python:3.14-slim

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

COPY --chown=10001:10001 manage.py ./
COPY --chown=10001:10001 src ./src

USER 10001:10001

CMD ["/bin/sh", "-c", "exec celery --app=config worker --loglevel=\"${CELERY_LOG_LEVEL:-INFO}\" --queues=mediamtx --hostname=\"worker@${HOSTNAME}\""]