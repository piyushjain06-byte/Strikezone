from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_guestuser_is_mobile_verified'),
    ]

    operations = [
        migrations.AddField(
            model_name='guestuser',
            name='plan',
            field=models.CharField(
                choices=[('free', 'Free'), ('pro', 'Pro'), ('pro_plus', 'Pro Plus')],
                default='free',
                max_length=20,
            ),
        ),
    ]
