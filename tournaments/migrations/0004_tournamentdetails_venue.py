from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tournaments', '0003_alter_tournamentaward_awarded_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='tournamentdetails',
            name='venue',
            field=models.CharField(
                blank=True,
                help_text='Full address of the venue (e.g. Wankhede Stadium, Mumbai)',
                max_length=300,
            ),
        ),
        migrations.AddField(
            model_name='tournamentdetails',
            name='venue_lat',
            field=models.DecimalField(
                blank=True,
                decimal_places=7,
                help_text='Latitude (auto-filled by Google Maps)',
                max_digits=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='tournamentdetails',
            name='venue_lng',
            field=models.DecimalField(
                blank=True,
                decimal_places=7,
                help_text='Longitude (auto-filled by Google Maps)',
                max_digits=10,
                null=True,
            ),
        ),
    ]
