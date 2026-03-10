#!/usr/bin/env bash
set -o errexit
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate

# Create superuser automatically if it doesn't exist
python manage.py shell << 'PYEOF'
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='ceo').exists():
    User.objects.create_superuser('ceo', 'ceo@strikezone.com', 'Strikezone@123')
    print("Superuser created: username=ceo password=Strikezone@123")
else:
    print("Superuser already exists")
PYEOF