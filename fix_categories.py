import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'strikezone.settings')
django.setup()

from tournaments.models import UpperCategory

categories = [
    'Home',
    'Tournaments',
    'Manage Cricket',
    'Create Match',
    'Start Tournament',
    'My Profile',
    'My Matches',
]

for name in categories:
    obj, created = UpperCategory.objects.get_or_create(category_name=name)
    print(f"{'Created' if created else 'Already exists'}: {name}")

print(f"\nDone. Total categories in DB: {UpperCategory.objects.count()}")
