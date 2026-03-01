from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scoring', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='ball',
            name='shot_direction',
            field=models.CharField(
                blank=True,
                choices=[
                    ('FINE_LEG', 'Fine Leg'),
                    ('SQUARE_LEG', 'Square Leg'),
                    ('MID_WICKET', 'Mid Wicket'),
                    ('MID_ON', 'Mid On'),
                    ('STRAIGHT', 'Straight'),
                    ('MID_OFF', 'Mid Off'),
                    ('COVER', 'Cover'),
                    ('POINT', 'Point'),
                    ('THIRD_MAN', 'Third Man'),
                    ('LONG_ON', 'Long On'),
                    ('LONG_OFF', 'Long Off'),
                    ('FINE_LEG_DEEP', 'Fine Leg Deep'),
                ],
                max_length=20,
                null=True,
            ),
        ),
    ]
