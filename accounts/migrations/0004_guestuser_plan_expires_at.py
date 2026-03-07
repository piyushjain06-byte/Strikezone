from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_guestuser_plan'),
    ]

    operations = [
        migrations.AddField(
            model_name='guestuser',
            name='plan_expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
