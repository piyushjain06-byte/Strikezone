#!/usr/bin/env bash
set -o errexit

# Install all dependencies
pip install --no-cache-dir -r requirements.txt

# Collect static files
python manage.py collectstatic --noinput

# Create media folders
mkdir -p media/guest_photos media/player_photos media/team_logos