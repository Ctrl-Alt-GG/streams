# Streams

Network-private Django directory for streams served by an external MediaMTX deployment.
Catalog pages, publishing guidance, playback URLs, and read-only API endpoints are
anonymous and require a private ingress, VPN, or equivalent network boundary.
Staff-only API writes use the authenticated Django admin session.

MediaMTX 1.21 or newer is required. A missing `available` field is reported as an
upstream schema error, retaining the last successful snapshot. Invalid or overlong
publisher paths are skipped, so one publisher cannot take down the directory.
Stream records and links survive disconnects. Administrators can hide channels
from the stream list and unhide them; hiding a channel does not restrict playback at
the media server. They can delete channels with the list's bulk action, while a
recent, successful check of the media server confirms that their paths are gone
and no other check is under way. Hidden channels have to be unhidden first, as the
check leaves them out.
A channel that hasn't been live for `STREAM_HIDE_OFFLINE_AFTER_DAYS` days (14 by
default, 0 keeps every channel) leaves the catalog; its page and address stay.

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
The application port is published to loopback only. Set `TIME_ZONE` for
organizer-facing admin timestamps (defaults to Europe/Budapest).

Failed admin logins are counted by [django-axes](https://django-axes.readthedocs.io/)
per username and client address: five failures lock that username for fifteen
minutes, for that address only, so nobody can lock an organizer out from elsewhere.
The client address is the last `X-Forwarded-For` entry on requests from
`TRUSTED_PROXIES`, and the socket address otherwise. The host proxy reaches the
published port through the gateway of the compose network, which has the fixed
subnet `172.31.241.0/24`, so the default is its gateway `172.31.241.1`. If that
subnet is taken on the host, change it in `compose.yaml` and set `TRUSTED_PROXIES`
to the new gateway. The proxy must add the client address as the last
`X-Forwarded-For` entry, as nginx's `$proxy_add_x_forwarded_for` does.

Compose passes `.env` to the application containers only. Postgres, Redis and
RabbitMQ receive their own variables, and the capture worker, which decodes video
from publishers, runs with `config.settings.capture`: no database, and only
`REDIS_URL`, `CELERY_BROKER_URL`, the `MEDIAMTX_HLS_*` addresses, the
`STREAM_THUMBNAIL_*` settings and `LOG_LEVEL`.

A check of the media server has `MEDIAMTX_RECONCILE_BUDGET_SECONDS` (six) in all,
however many pages of paths it reads, and ends before Celery stops the task. Each thumbnail scan queues at most
`STREAM_THUMBNAIL_CAPTURES_PER_SCAN` captures (3 by default, what one capture worker
manages between scans), the oldest thumbnails first. `manage.py check` refuses
addresses without a supported scheme and durations that contradict each other; the
timing settings and how they relate are described in `config/settings/base.py`.

Catalog and channel pages check the status every ten seconds. The catalog reloads
when a channel appears or goes away, or when a fresh status check reports a change.
A channel page reloads only for a status change that a fresh check confirms, so
playback continues while the status source is briefly unavailable, for example
during a Redis restart. A new display name appears in place; a new description
waits for the next visit while the player is on the page. When playback fails, the
player tries the stream again with exponential backoff and keeps playing audio that
was started once the stream is back; the page reloads only when a fresh check
confirms that the channel went offline.

The publishing guide shows credential placeholders; actual credentials come from
the event organizer. Special characters in RTMP
query credentials must be URL-encoded, as described in the
[MediaMTX authentication documentation](https://mediamtx.org/docs/features/authentication).

## Internal access

Other services, such as the event signage, should read the API through the public
HTTPS address, for example `https://streams.example.com/api/v1`. That works with
all of the security settings above. To serve the API to containers on the same
host over the Docker network instead:

1. Create a shared network with `docker network create streams-internal`, and
   attach the web service to it in a `compose.override.yaml` with an alias that
   the other stack can resolve:

   ```yaml
   services:
     web:
       networks:
         default:
         streams-internal:
           aliases: [streams-web]
   networks:
     streams-internal:
       external: true
   ```

2. Add `streams-web` to `DJANGO_ALLOWED_HOSTS`.
3. Set `DJANGO_SECURE_REDIRECT_EXEMPT=^api/v1/,^streams/[^/]+/thumbnail/`, so that
   the API and thumbnails answer plain HTTP instead of redirecting to HTTPS. The
   admin still redirects.

The other stack then uses `http://streams-web:8000/api/v1`. That traffic is not
encrypted: any container on `streams-internal` can read and alter it.

## Checks

```sh
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check --settings=config.settings.test
uv run python manage.py makemigrations --check --dry-run --settings=config.settings.test
uv run python manage.py test catalog --settings=config.settings.test
node --test scripts/*.test.cjs
```

Tests tagged `integration` need PostgreSQL and Redis, for concurrent writes, locks
between processes and migrations of existing rows. `DATABASE_URL` names a server the
tests may create a database on, and `REDIS_URL` a Redis database they may empty:

```sh
uv run python manage.py test catalog --tag integration --settings=config.settings.integration
```

Tests tagged `browser` run the player in Chromium against streams they encode
themselves, VP9 and Opus in fMP4 segments as the media server serves HLS: video,
starting audio, recovering after the stream failed, and going back to a page.
They run with `STREAMS_BROWSER_TESTS=1`, after `uv run playwright install chromium`:

```sh
STREAMS_BROWSER_TESTS=1 uv run python manage.py test catalog --tag browser --settings=config.settings.test
```

CI also runs `manage.py check --deploy --fail-level WARNING` with the production
settings and placeholder values for their required variables.

The other tests use in-memory SQLite and an isolated in-memory cache, and skip the
integration and browser tests; deployed processes use Redis. Public cache reads degrade to unknown status and fallback thumbnails when
Redis is unreachable; login limits and worker locks retain strict failure
behavior: admin logins fail while Redis is down. Removing staff status immediately revokes API editing even if a user
retains a model permission and an existing session. Image publishing waits for these checks. Playback from the real MediaMTX server,
with the codecs publishers send, still needs that server and valid publisher
credentials.
