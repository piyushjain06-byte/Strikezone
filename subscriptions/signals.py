from django.db.models.signals import post_save
from django.contrib.auth.models import User
from django.dispatch import receiver
from .models import Subscription


@receiver(post_save, sender=User)
def create_subscription_for_new_user(sender, instance, created, **kwargs):
    """
    Every time a new Django User is created, automatically give them
    a Free subscription so we always have a row to check.
    """
    if created:
        Subscription.objects.get_or_create(user=instance)
