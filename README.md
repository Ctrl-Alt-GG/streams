# Streams

Network-private Django directory for streams served by an external MediaMTX deployment.
Catalog pages, publishing guidance, playback URLs, and read-only API endpoints are
anonymous and require a private ingress, VPN, or equivalent network boundary.
Staff-only API writes use the authenticated Django admin session.

MediaMTX 1.21 or newer is required. A missing `available` field is reported as an
upstream schema error, retaining the last successful snapshot. Invalid or overlong
publisher paths are skipped, so one publisher cannot take down the directory.
Stream records and links survive disconnects. Administrators can delete a channel
when a successful snapshot confirms its path is absent, or hide channels from the
stream list. Hiding a channel does not restrict playback at the media server.

## Static assets

Static assets use WhiteNoise and a local, hashed manifest. The web image builds
Tailwind and runs `collectstatic`; Django serves `/static/` from that image.
Route the entire application origin to the web container, including `/static/`.
MinIO and its credentials are no longer needed. Existing MinIO data can be retained
for rollback; the new stack does not delete it. Remove the old ingress mapping
from `/static/` to MinIO when deploying this version.

The pinned hls.js distribution (v1.7.4, with the optional source-map reference removed) and its license live under
`src/catalog/static/catalog/js/`. Browser scripts are served by this application;
the media server supplies HLS playlists and media only. Both video and audio use
the bundled player; upstream HTML is never embedded. The CSP rejects frames,
including frames from the catalog origin.

## Runtime and development

Copy `.env.example` to `.env` and set the credentials, hostnames, and MediaMTX
endpoints. All entry points (`manage.py`, Celery, WSGI and ASGI) consistently default to
production settings. Select `DJANGO_SETTINGS_MODULE=config.settings.development`
for local development.
The local debug settings accept only loopback hosts.

Both development and production need a shared Redis cache and RabbitMQ broker.
The status worker consumes `status-quorum`; the capture worker consumes
`thumbnails-quorum` with one process and container memory/PID limits. Run both
workers when developing outside Compose. Development settings do not provide
cross-process status updates through a local in-memory cache.

```sh
uv sync --frozen
export DJANGO_SETTINGS_MODULE=config.settings.development
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py tailwind build
uv run python manage.py collectstatic --noinput
uv run python manage.py runserver
```

The host reverse proxy must terminate TLS and preserve `X-Forwarded-Proto`.
The application port is published to loopback only. Admin logins are limited to
30 submissions per minute per socket client address; additionally rate limit at
the host proxy if it serves many clients through a shared address. Set `TIME_ZONE`
for organizer-facing admin timestamps (defaults to Europe/Budapest).

Catalog and detail pages check status every ten seconds and reload when the
visible channels change. Audio retries verify that the channel is still live,
with exponential backoff. The publishing guide shows credential placeholders;
actual credentials come from the event organizer. Special characters in RTMP
query credentials must be URL-encoded, as described in the
[MediaMTX authentication documentation](https://mediamtx.org/docs/features/authentication).

## Checks

```sh
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check --settings=config.settings.test
uv run python manage.py makemigrations --check --dry-run --settings=config.settings.test
uv run python manage.py test catalog --settings=config.settings.test
node scripts/player.test.cjs
```

Tests use in-memory SQLite and an isolated in-memory cache; deployed processes use
Redis. Public cache reads degrade to unknown status and fallback thumbnails when
Redis is unreachable; login throttles and worker locks retain strict failure
behavior. Removing staff status immediately revokes API editing even if a user
retains a model permission and an existing session. Image publishing waits for these checks. End-to-end playback still requires
the real MediaMTX server and valid publisher credentials.
