from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_guestfollow'),
    ]

    operations = [
        migrations.AddField(
            model_name='guestuser',
            name='display_name',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='guestuser',
            name='photo',
            field=models.ImageField(blank=True, null=True, upload_to='guest_photos/'),
        ),
    ]
