"""
Organizers edit a stream's name and description while the check of the media server records
what the stream carries and when it was live. Each saves only its own fields.
"""

from django.contrib import admin
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings

from catalog.admin import StreamAdmin
from catalog.api.serializers import StreamOverlaySerializer
from catalog.models import Stream
from catalog.tests.support import MediaServerMixin


def save_with_serializer(stream: Stream, **fields) -> None:
    serializer = StreamOverlaySerializer(stream, data=fields, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()


class OverlayWriteTests(MediaServerMixin, TestCase):
    def setUp(self):
        cache.clear()

    def test_an_edit_keeps_what_a_check_recorded_after_the_stream_was_loaded(self):
        stream = Stream.objects.create(path_name="live/main")
        loaded_for_edit = Stream.objects.get(pk=stream.pk)
        self.reconcile(["live/main"])
        save_with_serializer(loaded_for_edit, display_name="Main stage")
        stream.refresh_from_db()
        self.assertEqual(stream.display_name, "Main stage")
        self.assertEqual(stream.media_kind, Stream.MediaKind.VIDEO)
        self.assertIsNotNone(stream.last_seen_at)

    def test_edits_of_different_fields_keep_each_other(self):
        stream = Stream.objects.create(path_name="live/main")
        first = Stream.objects.get(pk=stream.pk)
        second = Stream.objects.get(pk=stream.pk)
        save_with_serializer(first, display_name="Main stage")
        save_with_serializer(second, description="Finals at 18:00")
        stream.refresh_from_db()
        self.assertEqual(stream.display_name, "Main stage")
        self.assertEqual(stream.description, "Finals at 18:00")

    @override_settings(LANGUAGE_CODE="en")
    def test_the_admin_saves_only_what_was_changed_in_its_form(self):
        stream = Stream.objects.create(path_name="live/main", display_name="Main")
        loaded_for_edit = Stream.objects.get(pk=stream.pk)
        Stream.objects.filter(pk=stream.pk).update(display_name="Renamed meanwhile")
        self.reconcile(["live/main"])
        model_admin = StreamAdmin(Stream, admin.site)
        request = RequestFactory().post("/admin/")
        request.user = self.organizer()
        form = model_admin.get_form(request, loaded_for_edit, change=True)(
            data={"display_name": "Main", "description": "Finals"}, instance=loaded_for_edit
        )
        self.assertTrue(form.is_valid(), form.errors)
        model_admin.save_model(request, form.save(commit=False), form, change=True)
        stream.refresh_from_db()
        self.assertEqual(stream.description, "Finals")
        self.assertEqual(stream.display_name, "Renamed meanwhile")
        self.assertEqual(stream.media_kind, Stream.MediaKind.VIDEO)
        self.assertIsNotNone(stream.last_seen_at)

    def test_editorial_values_are_normalized_before_they_are_saved(self):
        stream = Stream.objects.create(path_name="live/main")
        save_with_serializer(stream, display_name="  Main stage  ")
        stream.refresh_from_db()
        self.assertEqual(stream.display_name, "Main stage")

    def test_only_editorial_fields_are_saved_as_an_overlay(self):
        stream = Stream.objects.create(path_name="live/main")
        with self.assertRaises(ValueError):
            stream.save_overlay(["display_name", "media_kind"])
