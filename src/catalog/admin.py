from django.contrib import admin

from catalog.models import Stream


@admin.register(Stream)
class StreamAdmin(admin.ModelAdmin):
    list_display = ("effective_name", "path_name", "status", "updated_at")
    search_fields = ("path_name", "display_name")
    readonly_fields = ("id", "path_name", "created_at", "updated_at")
    fields = ("id", "path_name", "display_name", "created_at", "updated_at")

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    @admin.display(description="Status")
    def status(self, obj) -> str:
        from catalog.services.catalog import CatalogService

        try:
            return CatalogService().get(obj.pk)[1].status
        except Stream.DoesNotExist:
            return "unknown"
