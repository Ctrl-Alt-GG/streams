from django.contrib import admin
from django.contrib.admin.views.main import ChangeList
from django.utils.translation import gettext_lazy as _

from catalog.models import BlockedPath, Stream


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
        return False

    def get_changelist(self, request, **kwargs):
        return StreamChangeList

    @admin.display(description=_("Status"))
    def status(self, obj) -> str:
        return obj.catalog_status
