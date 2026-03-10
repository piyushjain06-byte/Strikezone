#!/usr/bin/env bash
set -o errexit
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate

# Seed UpperCategory nav items if they don't exist
python manage.py shell << 'PYEOF'
from tournaments.models import UpperCategory
from django.contrib.auth import get_user_model

nav_items = ['Home', 'Tournaments', 'Manage Cricket', 'Create Match', 'Start Tournament']
for name in nav_items:
    obj, created = UpperCategory.objects.get_or_create(category_name=name)
    if created:
        print(f"Created nav item: {name}")
    else:
        print(f"Already exists: {name}")

# Create superuser if not exists
User = get_user_model()
if not User.objects.filter(username='ceo').exists():
    User.objects.create_superuser('ceo', 'ceo@strikezone.com', 'Strikezone@123')
    print("Superuser created: username=ceo password=Strikezone@123")
else:
    print("Superuser already exists")
PYEOF