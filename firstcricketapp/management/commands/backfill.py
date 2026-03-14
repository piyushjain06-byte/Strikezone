from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Placeholder backfill command'

    def handle(self, *args, **kwargs):
        self.stdout.write('No backfill operations defined.')