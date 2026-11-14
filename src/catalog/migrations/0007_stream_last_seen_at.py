import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0006_alter_blockedpath_options"),
    ]

    operations = [
        # Channels that exist already count as seen now, so none of them drops out of the catalog
        # before it has had the full time to come back.
        migrations.AddField(
            model_name="stream",
            name="last_seen_at",
            field=models.DateTimeField(
                blank=True,
                default=django.utils.timezone.now,
                editable=False,
                null=True,
                verbose_name="Last seen live",
            ),
            preserve_default=False,
        ),
    ]
