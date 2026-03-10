from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('scoring', '0002_ball_shot_direction'),
        ('teams', '0001_initial'),
        ('matches', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='HatTrick',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('innings', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hat_tricks', to='scoring.innings')),
                ('bowler', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hat_tricks', to='teams.playerdetails')),
                ('ball1', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hat_trick_ball1', to='scoring.ball')),
                ('ball2', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hat_trick_ball2', to='scoring.ball')),
                ('ball3', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hat_trick_ball3', to='scoring.ball')),
                ('victim1', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hat_trick_victim1', to='teams.playerdetails')),
                ('victim2', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hat_trick_victim2', to='teams.playerdetails')),
                ('victim3', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hat_trick_victim3', to='teams.playerdetails')),
                ('match', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hat_tricks', to='matches.creatematch')),
            ],
            options={
                'unique_together': {('innings', 'ball3')},
            },
        ),
    ]
