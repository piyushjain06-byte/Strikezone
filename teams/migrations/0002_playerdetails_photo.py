from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('teams', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='playerdetails',
            name='photo',
            field=models.ImageField(blank=True, help_text='Optional profile photo (jpg, png)', null=True, upload_to='player_photos/'),
        ),
    ]