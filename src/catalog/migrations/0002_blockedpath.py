from django.db import migrations, models


def add_default_blocked_path(apps, schema_editor) -> None:
    blocked_path = apps.get_model("catalog", "BlockedPath")
    blocked_path.objects.get_or_create(path_name="all_others")


def remove_default_blocked_path(apps, schema_editor) -> None:
    blocked_path = apps.get_model("catalog", "BlockedPath")
    blocked_path.objects.filter(path_name="all_others").delete()


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="BlockedPath",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("path_name", models.CharField(max_length=512, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ("path_name",)},
        ),
        migrations.RunPython(add_default_blocked_path, remove_default_blocked_path),
    ]
