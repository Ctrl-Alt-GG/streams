from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from redis.exceptions import ConnectionError as RedisConnectionError

PROXY = "172.31.241.1"


@override_settings(TRUSTED_PROXIES=[PROXY])
class AdminLoginLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser("organizer", "organizer@example.org", "secret")

    def log_in(self, password, **meta):
        return self.client.post(
            "/admin/login/", {"username": "organizer", "password": password}, **meta
        )

    def test_failures_lock_the_username_only_for_the_address_they_came_from(self):
        for _ in range(4):
            self.assertEqual(self.log_in("wrong", REMOTE_ADDR="198.51.100.7").status_code, 200)
        self.assertEqual(self.log_in("wrong", REMOTE_ADDR="198.51.100.7").status_code, 429)
        self.assertEqual(self.log_in("secret", REMOTE_ADDR="198.51.100.7").status_code, 429)
        self.assertEqual(self.log_in("secret", REMOTE_ADDR="203.0.113.9").status_code, 302)

    def test_clients_behind_the_trusted_proxy_are_counted_separately(self):
        for _ in range(5):
            self.log_in("wrong", HTTP_X_FORWARDED_FOR="198.51.100.7", REMOTE_ADDR=PROXY)
        locked = self.log_in("secret", HTTP_X_FORWARDED_FOR="198.51.100.7", REMOTE_ADDR=PROXY)
        self.assertEqual(locked.status_code, 429)
        # The proxy appends the address it served; earlier entries come from the client.
        forged = self.log_in(
            "secret", HTTP_X_FORWARDED_FOR="203.0.113.9, 198.51.100.7", REMOTE_ADDR=PROXY
        )
        self.assertEqual(forged.status_code, 429)
        other = self.log_in("secret", HTTP_X_FORWARDED_FOR="203.0.113.9", REMOTE_ADDR=PROXY)
        self.assertEqual(other.status_code, 302)

    def test_x_forwarded_for_from_other_addresses_is_ignored(self):
        for index in range(5):
            self.log_in(
                "wrong", REMOTE_ADDR="198.51.100.7", HTTP_X_FORWARDED_FOR=f"203.0.113.{index}"
            )
        response = self.log_in(
            "secret", REMOTE_ADDR="198.51.100.7", HTTP_X_FORWARDED_FOR="203.0.113.50"
        )
        self.assertEqual(response.status_code, 429)

    def test_logins_fail_while_the_cache_is_down(self):
        with (
            patch("django.core.cache.cache.get", side_effect=RedisConnectionError("unavailable")),
            self.assertRaises(RedisConnectionError),
        ):
            self.log_in("secret", REMOTE_ADDR="198.51.100.7")
