from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tournaments', '0008_tournamenthire'),
    ]

    operations = [
        migrations.AddField(
            model_name='tournamentdetails',
            name='teams_editable',
            field=models.BooleanField(
                default=False,
                help_text='If True, players can be moved between teams during the tournament.'
            ),
        ),
    ]
