from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_guestuser_role'),
        ('teams', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PlayerFollow',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('follower', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='following_set',
                    to='teams.playerdetails',
                )),
                ('following', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='followers_set',
                    to='teams.playerdetails',
                )),
            ],
            options={
                'ordering': ['-created_at'],
                'unique_together': {('follower', 'following')},
            },
        ),
    ]
