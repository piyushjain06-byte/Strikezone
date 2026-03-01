from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('tournaments', '0001_initial'),
        ('teams', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='TournamentAward',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('award_type', models.CharField(choices=[('MOT', 'Man of the Tournament'), ('BBAT', 'Best Batsman'), ('BBOL', 'Best Bowler')], max_length=10)),
                ('score', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('total_runs', models.PositiveIntegerField(default=0)),
                ('total_balls_faced', models.PositiveIntegerField(default=0)),
                ('batting_avg', models.DecimalField(decimal_places=2, default=0, max_digits=7)),
                ('batting_sr', models.DecimalField(decimal_places=2, default=0, max_digits=7)),
                ('highest_score', models.PositiveIntegerField(default=0)),
                ('total_wickets', models.PositiveIntegerField(default=0)),
                ('bowling_avg', models.DecimalField(decimal_places=2, default=0, max_digits=7)),
                ('bowling_economy', models.DecimalField(decimal_places=2, default=0, max_digits=7)),
                ('best_bowling', models.CharField(default='0/0', max_length=10)),
                ('matches_played', models.PositiveIntegerField(default=0)),
                ('awarded_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('tournament', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='awards', to='tournaments.tournamentdetails')),
                ('player', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tournament_awards', to='teams.playerdetails')),
            ],
            options={
                'verbose_name': 'Tournament Award',
                'verbose_name_plural': 'Tournament Awards',
                'unique_together': {('tournament', 'award_type')},
            },
        ),
    ]
