from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('matches', '0001_initial'),
        ('teams', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ManOfTheMatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uii_score', models.DecimalField(decimal_places=2, default=0, max_digits=8)),
                ('bat_runs', models.PositiveIntegerField(default=0)),
                ('bat_balls', models.PositiveIntegerField(default=0)),
                ('bat_fours', models.PositiveIntegerField(default=0)),
                ('bat_sixes', models.PositiveIntegerField(default=0)),
                ('bowl_wickets', models.PositiveIntegerField(default=0)),
                ('bowl_runs', models.PositiveIntegerField(default=0)),
                ('bowl_overs', models.CharField(default='0', max_length=10)),
                ('awarded_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('match', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='man_of_the_match',
                    to='matches.creatematch',
                )),
                ('player', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='mom_awards',
                    to='teams.playerdetails',
                )),
            ],
            options={
                'verbose_name': 'Man of the Match',
                'verbose_name_plural': 'Man of the Match Awards',
            },
        ),
    ]
