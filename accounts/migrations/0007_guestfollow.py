from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_playerfollow'),
        ('teams', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='GuestFollow',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('guest', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='guest_following_set', to='accounts.guestuser')),
                ('following', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='guest_followers_set', to='teams.playerdetails')),
            ],
            options={
                'ordering': ['-created_at'],
                'unique_together': {('guest', 'following')},
            },
        ),
    ]
