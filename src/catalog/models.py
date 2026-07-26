import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


def get_stream_display_name(path_name: str, display_name: str = "") -> str:
    friendly_path_name = path_name.removeprefix("live/")
    return display_name or friendly_path_name or path_name


class BlockedPath(models.Model):
    path_name = models.CharField(max_length=512, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("path_name",)

    def __str__(self) -> str:
        return self.path_name

    def clean(self) -> None:
        super().clean()
        self.path_name = self.path_name.strip()
        if not self.path_name:
            raise ValidationError({"path_name": _("A blocked path name cannot be empty.")})


class Stream(models.Model):
    class MediaKind(models.TextChoices):
        UNKNOWN = "unknown", _("Unknown")
        AUDIO = "audio", _("Audio")
        VIDEO = "video", _("Video")

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    path_name = models.CharField(max_length=512, unique=True, editable=False)
    display_name = models.CharField(max_length=200, blank=True)
    media_kind = models.CharField(
        max_length=7,
        choices=MediaKind,
        default=MediaKind.UNKNOWN,
        editable=False,
    )
    description = models.TextField(
        blank=True,
        help_text=_("Markdown is supported."),
        verbose_name=_("Description"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("path_name",)

    def __str__(self) -> str:
        return self.effective_name

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def effective_name(self) -> str:
        return get_stream_display_name(self.path_name, self.display_name)

    def clean(self) -> None:
        super().clean()
        self.display_name = self.display_name.strip()
        self.description = self.description.strip()
        if self.pk:
            original = (
                type(self).objects.filter(pk=self.pk).values_list("path_name", flat=True).first()
            )
            if original is not None and original != self.path_name:
                raise ValidationError({"path_name": _("Stream path names are immutable.")})
