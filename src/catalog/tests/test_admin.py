from unittest.mock import patch

from django.contrib import admin
from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from catalog.admin import StreamAdmin
from catalog.models import BlockedPath, Stream
from catalog.services.cache import reconcile_lock
from catalog.services.catalog import CatalogService
from catalog.services.observations import record_failure, utc_now
from catalog.services.streams import delete_absent_streams
from catalog.tests.support import MediaServerMixin


# The admin is in LANGUAGE_CODE, as i18n_patterns doesn't cover it.
@override_settings(LANGUAGE_CODE="en")
class StreamAdminTests(MediaServerMixin, TestCase):
    changelist = "admin:catalog_stream_changelist"

    def setUp(self):
        cache.clear()

    def run_action(self, action, streams):
        return self.client.post(
            reverse(self.changelist),
            {"action": action, "_selected_action": [str(stream.pk) for stream in streams]},
            follow=True,
        )

    def test_list_shows_hidden_streams_as_hidden(self):
        live = Stream.objects.create(path_name="live/main")
        hidden = Stream.objects.create(path_name="live/hidden")
        BlockedPath.objects.create(path_name=hidden.path_name)
        self.reconcile([live.path_name, hidden.path_name])
        self.client.force_login(self.organizer())
        response = self.client.get(reverse(self.changelist))
        statuses = {
            stream.path_name: stream.catalog_status for stream in response.context["cl"].result_list
        }
        self.assertEqual(statuses, {"live/main": "live", "live/hidden": "hidden"})
        self.assertContains(response, '<td class="field-status">Hidden</td>', html=True)
        self.assertContains(response, '<td class="field-status">Live</td>', html=True)

    def test_list_reads_statuses_without_projecting_the_catalog(self):
        Stream.objects.create(path_name="live/main")
        self.reconcile(["live/main"])
        self.client.force_login(self.organizer())
        with patch.object(CatalogService, "_project", side_effect=AssertionError("projected")):
            response = self.client.get(reverse(self.changelist))
        self.assertContains(response, '<td class="field-status">Live</td>', html=True)

    def test_deleting_a_stream_needs_a_recent_check_that_its_path_is_gone(self):
        stream = Stream.objects.create(path_name="live/absent")

        def deletable():
            outcome = delete_absent_streams([stream.pk])
            return not isinstance(outcome, str) and stream in outcome.absent

        self.assertEqual(delete_absent_streams([stream.pk]), "unconfirmed")
        self.reconcile([stream.path_name])
        self.assertFalse(deletable())
        self.reconcile([])
        record_failure("timeout", utc_now())
        self.assertEqual(delete_absent_streams([stream.pk]), "unconfirmed")
        self.reconcile([])
        with self.settings(MEDIAMTX_FRESHNESS_SECONDS=0):
            self.assertEqual(delete_absent_streams([stream.pk]), "unconfirmed")
        BlockedPath.objects.create(path_name=stream.path_name)
        self.assertFalse(deletable())
        BlockedPath.objects.all().delete()
        self.assertTrue(deletable())
        self.assertFalse(Stream.objects.exists())

    def test_nothing_is_deleted_while_the_media_server_is_being_checked(self):
        absent = Stream.objects.create(path_name="live/absent")
        self.reconcile([])
        self.client.force_login(self.organizer())
        with reconcile_lock() as acquired:
            self.assertTrue(acquired)
            response = self.run_action("delete_absent_streams", [absent])
        self.assertTrue(Stream.objects.filter(pk=absent.pk).exists())
        self.assertContains(response, "the media server is being checked right now")

    def test_bulk_delete_removes_only_streams_the_media_server_no_longer_has(self):
        present = Stream.objects.create(path_name="live/present")
        absent = Stream.objects.create(path_name="live/absent")
        hidden = Stream.objects.create(path_name="live/hidden")
        BlockedPath.objects.create(path_name=hidden.path_name)
        self.client.force_login(self.organizer())
        selection = [present, absent, hidden]
        response = self.run_action("delete_absent_streams", selection)
        self.assertEqual(Stream.objects.count(), 3)
        self.assertContains(response, "Nothing was deleted")
        self.reconcile([present.path_name])
        response = self.run_action("delete_absent_streams", selection)
        self.assertEqual(
            sorted(Stream.objects.values_list("path_name", flat=True)),
            ["live/hidden", "live/present"],
        )
        self.assertEqual(
            [str(message) for message in response.context["messages"]],
            [
                "Deleted 1 stream.",
                "Kept, as the media server still has them: present",
                "Kept, as hidden streams aren't checked on the media server: hidden. "
                "Unhide them, then delete them after the next check.",
            ],
        )
        self.assertTrue(
            LogEntry.objects.filter(action_flag=DELETION, object_id=str(absent.pk)).exists()
        )

    def test_a_stream_is_deleted_from_the_list_only(self):
        stream = Stream.objects.create(path_name="live/absent")
        self.reconcile([])
        model_admin = StreamAdmin(Stream, admin.site)
        request = RequestFactory().get("/admin/")
        request.user = self.organizer()
        self.assertTrue(model_admin.has_delete_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request, stream))

    def test_actions_are_offered_by_permission(self):
        user = get_user_model().objects.create_user("staff", password="password", is_staff=True)
        user.user_permissions.add(
            *Permission.objects.filter(codename__in=["view_stream", "change_stream"])
        )
        self.client.force_login(user)

        def offered():
            form = self.client.get(reverse(self.changelist)).context["action_form"]
            return set() if form is None else {name for name, _ in form.fields["action"].choices}

        self.assertEqual(offered(), set())
        user.user_permissions.add(
            *Permission.objects.filter(codename__in=["add_blockedpath", "delete_blockedpath"])
        )
        self.assertEqual(offered(), {"", "hide_streams", "unhide_streams"})
        user.user_permissions.add(Permission.objects.get(codename="delete_stream"))
        self.assertEqual(offered(), {"", "hide_streams", "unhide_streams", "delete_absent_streams"})

    def test_unhide_shows_hidden_streams_again(self):
        stream = Stream.objects.create(path_name="live/hidden")
        BlockedPath.objects.create(path_name=stream.path_name)
        self.client.force_login(self.organizer())
        response = self.run_action("unhide_streams", [stream])
        self.assertFalse(BlockedPath.objects.filter(path_name=stream.path_name).exists())
        self.assertContains(response, "Selected streams are no longer hidden.")
        self.assertEqual(CatalogService().get(stream.id)[1].path_name, stream.path_name)
