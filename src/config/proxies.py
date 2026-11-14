import ipaddress
from contextlib import suppress

from django.conf import settings
from django.http import HttpRequest


def client_ip_address(request: HttpRequest) -> str:
    """
    The address of whoever sent the request.

    The host's reverse proxy reaches the web container through the Docker network's gateway, so
    every proxied request has that socket address. The proxy appends the address it served to
    X-Forwarded-For, which therefore ends with the real client; earlier entries come from the
    client and prove nothing. Only a request from one of TRUSTED_PROXIES gets that last entry.
    Anyone else is their own socket address, whatever header they send.
    """
    remote = request.META.get("REMOTE_ADDR", "")
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if remote in settings.TRUSTED_PROXIES and forwarded:
        with suppress(ValueError):
            return str(ipaddress.ip_address(forwarded.rsplit(",", 1)[-1].strip()))
    return remote
