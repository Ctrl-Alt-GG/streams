from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"

    def ready(self) -> None:
        from catalog import checks  # noqa: F401  Registers the checks of the settings.
