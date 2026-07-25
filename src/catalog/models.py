import uuid

from django.core.exceptions import ValidationError
from django.db import models


class Stream(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    path_name = models.CharField(max_length=512, unique=True, editable=False)
    display_name = models.CharField(max_length=200, blank=True)
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
        return self.display_name or self.path_name

    def clean(self) -> None:
        super().clean()
        self.display_name = self.display_name.strip()
        if self.pk:
            original = (
                type(self).objects.filter(pk=self.pk).values_list("path_name", flat=True).first()
            )
            if original is not None and original != self.path_name:
                raise ValidationError({"path_name": "MediaMTX path names are immutable."})
