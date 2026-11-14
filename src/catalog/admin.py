from django.contrib import admin, messages
from django.contrib.admin.views.main import ChangeList
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from catalog.models import BlockedPath, Stream
from catalog.services.catalog import CatalogService

STATUS_LABELS = {
    "live": _("Live"),
    "offline": _("Offline"),
    "unknown": _("Unknown"),
    "hidden": _("Hidden"),
}


class StreamChangeList(ChangeList):
    def get_results(self, request) -> None:
        super().get_results(request)
        catalog = CatalogService().list(include_unseen=True)
        statuses = {stream.id: stream.status for stream in catalog.streams}
        hidden = set(BlockedPath.objects.values_list("path_name", flat=True))
        for stream in self.result_list:
            stream.catalog_status = (
                "hidden" if stream.path_name in hidden else statuses.get(str(stream.pk), "unknown")
            )


def sort_for_deletion(streams: list[Stream]) -> dict[str, list[Stream]] | None:
    """
    Sorts `streams` into those that can be deleted ("absent") and those that are kept
    ("published" and "hidden"). None while the media server hasn't been checked recently.

    A stream can be deleted while a recent, successful check of the media server confirms that
    its path is gone. Otherwise the stream might be back, and the next check would recreate it
    under a new address, without its name and description. The check leaves hidden paths out.
    """
    snapshot = CatalogService().fresh_snapshot()
    if snapshot is None:
        return None
    hidden = set(
        BlockedPath.objects.filter(
            path_name__in=[stream.path_name for stream in streams]
        ).values_list("path_name", flat=True)
    )
    groups = {"absent": [], "published": [], "hidden": []}
    for stream in streams:
        if stream.path_name in hidden:
            groups["hidden"].append(stream)
        elif stream.path_name in snapshot["paths"]:
            groups["published"].append(stream)
        else:
            groups["absent"].append(stream)
    return groups


@admin.register(BlockedPath)
class BlockedPathAdmin(admin.ModelAdmin):
    list_display = ("path_name", "created_at")
    search_fields = ("path_name",)
    readonly_fields = ("created_at",)
    fields = ("path_name", "created_at")


@admin.register(Stream)
class StreamAdmin(admin.ModelAdmin):
    actions = ("hide_streams", "unhide_streams", "delete_absent_streams")
    list_display = ("effective_name", "path_name", "status", "last_seen_at", "updated_at")
    search_fields = ("path_name", "display_name", "description")
    readonly_fields = ("id", "path_name", "last_seen_at", "created_at", "updated_at")
    fields = (
        "id",
        "path_name",
        "display_name",
        "description",
        "last_seen_at",
        "created_at",
        "updated_at",
    )

    @admin.action(description=_("Hide selected streams from the catalog"), permissions=["hide"])
    def hide_streams(self, request, queryset):
        for stream in queryset:
            BlockedPath.objects.get_or_create(path_name=stream.path_name)
        self.message_user(request, _("Selected streams are hidden. Playback remains accessible."))

    @admin.action(description=_("Unhide selected streams"), permissions=["unhide"])
    def unhide_streams(self, request, queryset):
        BlockedPath.objects.filter(path_name__in=queryset.values("path_name")).delete()
        self.message_user(request, _("Selected streams are no longer hidden."))

    @admin.action(
        description=_("Delete selected streams that the media server no longer has"),
        permissions=["delete"],
    )
    def delete_absent_streams(self, request, queryset):
        groups = sort_for_deletion(list(queryset))
        if groups is None:
            message = _(
                "Nothing was deleted, as the media server's streams can't be confirmed right now. "
                "Try again in a minute."
            )
            self.message_user(request, message, messages.ERROR)
            return
        if absent := groups["absent"]:
            deleted = queryset.filter(pk__in=[stream.pk for stream in absent])
            self.log_deletions(request, deleted)
            self.delete_queryset(request, deleted)
            message = ngettext(
                "Deleted %(count)d stream.", "Deleted %(count)d streams.", len(absent)
            )
            self.message_user(request, message % {"count": len(absent)}, messages.SUCCESS)
        if groups["published"]:
            message = _("Kept, as the media server still has them: %(names)s")
            self.message_user(request, message % _names(groups["published"]), messages.WARNING)
        if groups["hidden"]:
            message = _(
                "Kept, as hidden streams aren't checked on the media server: %(names)s. "
                "Unhide them, then delete them after the next check."
            )
            self.message_user(request, message % _names(groups["hidden"]), messages.WARNING)

    def has_add_permission(self, request) -> bool:
        return False

    def has_hide_permission(self, request) -> bool:
        return self.has_change_permission(request) and request.user.has_perm(
            "catalog.add_blockedpath"
        )

    def has_unhide_permission(self, request) -> bool:
        return self.has_change_permission(request) and request.user.has_perm(
            "catalog.delete_blockedpath"
        )

    def has_delete_permission(self, request, obj=None) -> bool:
        if not super().has_delete_permission(request, obj):
            return False
        # Without `obj`, this decides whether the delete action is offered. It sorts each stream.
        if obj is None:
            return True
        groups = sort_for_deletion([obj])
        return groups is not None and bool(groups["absent"])

    def get_actions(self, request, action_location=admin.ActionLocation.CHANGE_LIST):
        actions = super().get_actions(request, action_location)
        # Replaced by delete_absent_streams: Django's own action refuses the whole selection when
        # one stream can't be deleted, without saying which or why.
        actions.pop("delete_selected", None)
        return actions

    def get_changelist(self, request, **kwargs):
        return StreamChangeList

    @admin.display(description=_("Status"))
    def status(self, obj) -> str:
        return STATUS_LABELS[obj.catalog_status]


def _names(streams: list[Stream]) -> dict:
    return {"names": ", ".join(stream.effective_name for stream in streams)}
