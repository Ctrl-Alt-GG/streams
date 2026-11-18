import ssl
import time
from collections.abc import Callable, Iterator
from typing import Any, TypeVar

import httpx
from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from catalog.services.observations import MediaTracks


class MediaMTXError(Exception):
    pass


class MediaMTXSchemaError(MediaMTXError):
    pass


class ActivePath(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    conf_name: str | None = Field(default=None, alias="confName")
    available: bool
    available_time: str | None = Field(default=None, alias="availableTime")
    online: bool | None = None
    online_time: str | None = Field(default=None, alias="onlineTime")
    source: dict[str, Any] | None = None
    tracks2: MediaTracks = Field(default_factory=list)
    readers: list[dict[str, Any]] = Field(default_factory=list)
    inbound_bytes: int = Field(default=0, alias="inboundBytes", ge=0)
    outbound_bytes: int = Field(default=0, alias="outboundBytes", ge=0)


class ConfiguredPath(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str


class Page(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    item_count: int = Field(alias="itemCount", ge=0)
    page_count: int = Field(alias="pageCount", ge=0)
    items: list[dict[str, Any]]


ItemT = TypeVar("ItemT", ActivePath, ConfiguredPath)


class MediaMTXClient:
    """
    Reads the media server's paths. A `deadline` (by `clock`, `time.monotonic` by default) bounds
    everything a call does: each request gets the time that is left, and none starts after it.
    """

    def __init__(
        self, client: httpx.Client | None = None, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._clock = clock
        self._owns_client = client is None
        verify: bool | ssl.SSLContext = settings.MEDIAMTX_API_VERIFY_TLS
        if settings.MEDIAMTX_API_CA_BUNDLE:
            verify = ssl.create_default_context(cafile=settings.MEDIAMTX_API_CA_BUNDLE)
        timeout = _timeout(
            settings.MEDIAMTX_API_CONNECT_TIMEOUT, settings.MEDIAMTX_API_READ_TIMEOUT
        )
        self._client = client or httpx.Client(
            base_url=f"{settings.MEDIAMTX_API_BASE_URL.rstrip('/')}/",
            auth=(settings.MEDIAMTX_API_USERNAME, settings.MEDIAMTX_API_PASSWORD),
            headers={"User-Agent": "streams-catalog/1.0"},
            http2=True,
            timeout=timeout,
            verify=verify,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def list_active_paths(self, *, deadline: float | None = None) -> list[ActivePath]:
        return list(self._paginate("v3/paths/list", ActivePath, deadline))

    def list_configured_paths(self, *, deadline: float | None = None) -> list[ConfiguredPath]:
        return list(self._paginate("v3/config/paths/list", ConfiguredPath, deadline))

    def _request_timeout(self, deadline: float | None) -> httpx.Timeout | None:
        if deadline is None:
            return None
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise httpx.TimeoutException("The check of the media server ran out of time.")
        return _timeout(
            min(settings.MEDIAMTX_API_CONNECT_TIMEOUT, remaining),
            min(settings.MEDIAMTX_API_READ_TIMEOUT, remaining),
        )

    def _paginate(
        self, endpoint: str, item_type: type[ItemT], deadline: float | None
    ) -> Iterator[ItemT]:
        page_number = 0
        seen: dict[str, ItemT] = {}
        while True:
            timeout = self._request_timeout(deadline)
            response = self._client.get(
                endpoint,
                params={"page": page_number, "itemsPerPage": 100},
                **({"timeout": timeout} if timeout is not None else {}),
            )
            response.raise_for_status()
            try:
                page = Page.model_validate(response.json())
                parsed_items = [item_type.model_validate(item) for item in page.items]
            except (ValueError, ValidationError) as error:
                raise MediaMTXSchemaError(
                    "The streaming service returned an invalid list response."
                ) from error

            if page.page_count == 0 and page.items:
                raise MediaMTXSchemaError("The streaming service returned items with zero pages.")
            if page.page_count and page_number >= page.page_count:
                break

            for item in parsed_items:
                seen[item.name] = item

            page_number += 1
            if page_number >= page.page_count:
                break

        yield from seen.values()


def _timeout(connect: float, read: float) -> httpx.Timeout:
    return httpx.Timeout(connect=connect, read=read, write=read, pool=connect)
