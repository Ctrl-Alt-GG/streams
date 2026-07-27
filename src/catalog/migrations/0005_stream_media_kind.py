from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0004_alter_stream_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="stream",
            name="media_kind",
            field=models.CharField(
                choices=[("unknown", "Unknown"), ("audio", "Audio"), ("video", "Video")],
                default="unknown",
                editable=False,
                max_length=7,
            ),
        ),
    ]
