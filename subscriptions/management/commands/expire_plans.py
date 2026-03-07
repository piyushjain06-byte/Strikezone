"""
Management command to auto-downgrade expired plans to Free.

Run daily via Task Scheduler:
    python manage.py expire_plans

Windows Task Scheduler setup:
    Program: python
    Arguments: manage.py expire_plans
    Start in: C:\path\to\your\pravas
    Schedule: Daily at midnight
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from accounts.models import GuestUser
from subscriptions.models import Subscription


class Command(BaseCommand):
    help = 'Downgrades expired paid plans back to Free'

    def handle(self, *args, **kwargs):
        now = timezone.now()
        count_players = 0
        count_admins  = 0

        # Downgrade expired GuestUser plans
        expired_players = GuestUser.objects.exclude(
            plan='free'
        ).filter(
            plan_expires_at__lt=now
        )
        for guest in expired_players:
            self.stdout.write(f"  Expiring {guest.mobile_number} ({guest.plan} → free)")
            guest.plan           = GuestUser.PLAN_FREE
            guest.plan_expires_at = None
            guest.save(update_fields=['plan', 'plan_expires_at'])
            count_players += 1

        # Downgrade expired Django admin subscriptions
        expired_admins = Subscription.objects.exclude(
            plan='free'
        ).filter(
            expires_at__lt=now,
            is_active=True,
        )
        for sub in expired_admins:
            self.stdout.write(f"  Expiring {sub.user.username} ({sub.plan} → free)")
            sub.plan = 'free'
            sub.save(update_fields=['plan'])
            count_admins += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Done. Expired {count_players} player plan(s) and {count_admins} admin plan(s).'
            )
        )
