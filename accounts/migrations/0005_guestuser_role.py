from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_guestuser_plan_expires_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='guestuser',
            name='role',
            field=models.CharField(
                choices=[('user', 'User'), ('employee', 'Employee')],
                default='user',
                max_length=20,
            ),
        ),
    ]
