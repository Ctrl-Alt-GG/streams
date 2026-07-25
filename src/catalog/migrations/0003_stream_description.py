from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_blockedpath"),
    ]

    operations = [
        migrations.AddField(
            model_name="stream",
            name="description",
            field=models.TextField(
                blank=True,
                help_text="Markdown is supported.",
                verbose_name="Description",
            ),
        ),
    ]
