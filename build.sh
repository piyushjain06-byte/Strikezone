#!/usr/bin/env bash
set -o errexit

# Install ML libs first separately (heavy — pin to avoid re-resolving)
pip install --no-cache-dir numpy==1.26.4 pandas==2.2.3 matplotlib==3.8.4 scikit-learn==1.4.2

# Install rest of dependencies
pip install --no-cache-dir -r requirements.txt

python manage.py collectstatic --noinput
python manage.py migrate
mkdir -p media/guest_photos media/player_photos media/team_logos

# Seed nav items and superuser
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

User = get_user_model()
if not User.objects.filter(username='ceo').exists():
    User.objects.create_superuser('ceo', 'ceo@strikezone.com', 'Strikezone@123')
    print("Superuser created: username=ceo password=Strikezone@123")
else:
    print("Superuser already exists")
PYEOF