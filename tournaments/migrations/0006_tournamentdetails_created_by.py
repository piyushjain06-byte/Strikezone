from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tournaments', '0005_tournamentdetails_is_force_completed'),
        ('teams', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='tournamentdetails',
            name='created_by_player',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='tournament_created_by_player',
                to='teams.playerdetails',
                help_text='Pro Plus player who created this tournament',
            ),
        ),
        migrations.AddField(
            model_name='tournamentdetails',
            name='created_by_admin',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='tournament_created_by_admin',
                to=settings.AUTH_USER_MODEL,
                help_text='Admin/CEO who created this tournament',
            ),
        ),
    ]