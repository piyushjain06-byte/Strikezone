from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('tournaments', '0004_tournamentdetails_venue'),
    ]
    operations = [
        migrations.AddField(
            model_name='tournamentdetails',
            name='is_force_completed',
            field=models.BooleanField(
                default=False,
                help_text='Manually mark tournament as completed (e.g. league-only or early completion).'
            ),
        ),
    ]
