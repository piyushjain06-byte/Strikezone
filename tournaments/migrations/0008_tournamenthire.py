from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('tournaments', '0007_remove_uppercategory_created_at_and_more'),
        ('teams', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='TournamentHire',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('hired_at', models.DateTimeField(auto_now_add=True)),
                ('tournament', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='hired_staff',
                    to='tournaments.tournamentdetails',
                )),
                ('hired_player', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='hired_for_tournaments',
                    to='teams.playerdetails',
                )),
            ],
            options={
                'verbose_name': 'Tournament Hire',
                'verbose_name_plural': 'Tournament Hires',
                'unique_together': {('tournament', 'hired_player')},
            },
        ),
    ]
