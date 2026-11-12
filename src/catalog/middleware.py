from hashlib import sha256

from django.core.cache import cache
from django.http import HttpResponse


class AdminLoginThrottleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path.rstrip("/") == "/admin/login":
            address = sha256(request.META.get("REMOTE_ADDR", "").encode()).hexdigest()
            key = f"catalog:admin-login:{address}"
            cache.add(key, 0, timeout=60)
            try:
                attempts = cache.incr(key)
            except ValueError:
                cache.set(key, 1, timeout=60)
                attempts = 1
            if attempts > 30:
                response = HttpResponse(
                    "Too many sign-in attempts. Try again in a minute.", status=429
                )
                response.headers["Retry-After"] = "60"
                return response
        return self.get_response(request)
