from collections.abc import Iterator
from typing import Any, TypeVar

import httpx
from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class MediaMTXError(Exception):
    pass


class MediaMTXSchemaError(MediaMTXError):
    pass


class ActivePath(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    conf_name: str | None = Field(default=None, alias="confName")
    available: bool = False
    available_time: str | None = Field(default=None, alias="availableTime")
    online: bool | None = None
    online_time: str | None = Field(default=None, alias="onlineTime")
    source: dict[str, Any] | None = None
    tracks2: list[dict[str, Any]] = Field(default_factory=list)
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
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._owns_client = client is None
        verify: bool | str = settings.MEDIAMTX_API_VERIFY_TLS
        if settings.MEDIAMTX_API_CA_BUNDLE:
            verify = settings.MEDIAMTX_API_CA_BUNDLE
        timeout = httpx.Timeout(
            connect=settings.MEDIAMTX_API_CONNECT_TIMEOUT,
            read=settings.MEDIAMTX_API_READ_TIMEOUT,
            write=settings.MEDIAMTX_API_READ_TIMEOUT,
            pool=settings.MEDIAMTX_API_CONNECT_TIMEOUT,
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

    def list_active_paths(self) -> list[ActivePath]:
        return list(self._paginate("v3/paths/list", ActivePath))

    def list_configured_paths(self) -> list[ConfiguredPath]:
        return list(self._paginate("v3/config/paths/list", ConfiguredPath))

    def _paginate(self, endpoint: str, item_type: type[ItemT]) -> Iterator[ItemT]:
        page_number = 0
        seen: dict[str, ItemT] = {}
        while True:
            response = self._client.get(
                endpoint,
                params={"page": page_number, "itemsPerPage": 100},
            )
            response.raise_for_status()
            try:
                page = Page.model_validate(response.json())
                parsed_items = [item_type.model_validate(item) for item in page.items]
            except (ValueError, ValidationError) as error:
                raise MediaMTXSchemaError("MediaMTX returned an invalid list response.") from error

            if page.page_count == 0 and page.items:
                raise MediaMTXSchemaError("MediaMTX returned items with zero pages.")
            if page.page_count and page_number >= page.page_count:
                raise MediaMTXSchemaError("MediaMTX returned an inconsistent page count.")

            for item in parsed_items:
                previous = seen.get(item.name)
                if previous is not None and previous != item:
                    raise MediaMTXSchemaError("MediaMTX returned conflicting duplicate paths.")
                if previous is None:
                    seen[item.name] = item
                    yield item

            page_number += 1
            if page_number >= page.page_count:
                break
