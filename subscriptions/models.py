from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Subscription(models.Model):

    PLAN_FREE     = 'free'
    PLAN_PRO      = 'pro'
    PLAN_PRO_PLUS = 'pro_plus'

    PLAN_CHOICES = [
        (PLAN_FREE,     'Free'),
        (PLAN_PRO,      'Pro'),
        (PLAN_PRO_PLUS, 'Pro Plus'),
    ]

    user       = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    plan       = models.CharField(max_length=20, choices=PLAN_CHOICES, default=PLAN_FREE)
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)  # null = never expires
    is_active  = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Subscription'
        verbose_name_plural = 'Subscriptions'

    def __str__(self):
        return f"{self.user.username} — {self.get_plan_display()}"

    def is_expired(self):
        """Returns True if the subscription has an expiry date that has passed."""
        if self.expires_at and timezone.now() > self.expires_at:
            return True
        return False

    def effective_plan(self):
        """
        Returns the real plan the user has right now.
        If the subscription is expired or inactive, falls back to 'free'.
        """
        if not self.is_active or self.is_expired():
            return self.PLAN_FREE
        return self.plan

    def can_use_ml(self):
        """Pro and Pro Plus can use ML analysis."""
        return self.effective_plan() in (self.PLAN_PRO, self.PLAN_PRO_PLUS)

    def can_use_crickbot(self):
        """Pro and Pro Plus can use CrickBot AI."""
        return self.effective_plan() in (self.PLAN_PRO, self.PLAN_PRO_PLUS)

    def can_manage_cricket(self):
        """Only Pro Plus can create/manage tournaments, matches, teams, players."""
        return self.effective_plan() == self.PLAN_PRO_PLUS
