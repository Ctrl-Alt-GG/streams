from django.contrib import admin
from django.contrib.admin.views.main import ChangeList
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _

from catalog.models import BlockedPath, Stream
from catalog.services.cache import get_snapshot


class StreamChangeList(ChangeList):
    def get_results(self, request) -> None:
        super().get_results(request)

        from catalog.services.catalog import CatalogService

        statuses = {stream.id: stream.status for stream in CatalogService().list().streams}
        for stream in self.result_list:
            stream.catalog_status = statuses.get(str(stream.pk), "unknown")


@admin.register(BlockedPath)
class BlockedPathAdmin(admin.ModelAdmin):
    list_display = ("path_name", "created_at")
    search_fields = ("path_name",)
    readonly_fields = ("created_at",)
    fields = ("path_name", "created_at")


@admin.register(Stream)
class StreamAdmin(admin.ModelAdmin):
    actions = ("hide_streams",)

    @admin.action(description=_("Hide selected streams from the catalog"), permissions=["change"])
    def hide_streams(self, request, queryset):
        if not request.user.has_perm("catalog.add_blockedpath"):
            raise PermissionDenied
        for stream in queryset:
            BlockedPath.objects.get_or_create(path_name=stream.path_name)
        self.message_user(request, _("Selected streams are hidden. Playback remains accessible."))

    list_display = ("effective_name", "path_name", "status", "updated_at")
    search_fields = ("path_name", "display_name", "description")
    readonly_fields = ("id", "path_name", "created_at", "updated_at")
    fields = (
        "id",
        "path_name",
        "display_name",
        "description",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        if not super().has_delete_permission(request, obj):
            return False
        snapshot = get_snapshot()
        return bool(
            snapshot is not None and obj is not None and obj.path_name not in snapshot["paths"]
        )

    def get_changelist(self, request, **kwargs):
        return StreamChangeList

    @admin.display(description=_("Status"))
    def status(self, obj) -> str:
        return obj.catalog_status
