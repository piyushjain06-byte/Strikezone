from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('matches', '0003_alter_manofthematch_awarded_at'),
    ]
    operations = [
        migrations.AddField(
            model_name='matchstart',
            name='custom_overs',
            field=models.PositiveIntegerField(
                blank=True, null=True,
                help_text='Override tournament overs for this match only. Set before 1st ball.'
            ),
        ),
    ]
