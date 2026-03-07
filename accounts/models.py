from django.db import models
from django.contrib.auth.hashers import check_password, identify_hasher, make_password


# ---------------------------------
# Guest User Model
# ---------------------------------
class GuestUser(models.Model):

    PLAN_FREE     = 'free'
    PLAN_PRO      = 'pro'
    PLAN_PRO_PLUS = 'pro_plus'

    PLAN_CHOICES = [
        (PLAN_FREE,     'Free'),
        (PLAN_PRO,      'Pro'),
        (PLAN_PRO_PLUS, 'Pro Plus'),
    ]

    ROLE_USER     = 'user'
    ROLE_EMPLOYEE = 'employee'
    ROLE_CHOICES  = [
        (ROLE_USER,     'User'),
        (ROLE_EMPLOYEE, 'Employee'),
    ]

    mobile_number      = models.CharField(max_length=15, unique=True)
    password           = models.CharField(max_length=128)
    is_mobile_verified = models.BooleanField(default=False)
    created_at         = models.DateTimeField(auto_now_add=True)
    plan               = models.CharField(max_length=20, choices=PLAN_CHOICES, default=PLAN_FREE)
    plan_expires_at    = models.DateTimeField(null=True, blank=True)
    role               = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_USER)

    class Meta:
        verbose_name_plural = 'Guest Users'

    def __str__(self):
        return f"Guest: {self.mobile_number} ({self.get_plan_display()})"

    def set_password(self, raw_password: str) -> None:
        self.password = make_password(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password(raw_password, self.password)

    def has_usable_password_hash(self) -> bool:
        try:
            identify_hasher(self.password)
            return True
        except Exception:
            return False

    def is_employee(self):
        return self.role == self.ROLE_EMPLOYEE

    def can_manage(self):
        return self.plan == self.PLAN_PRO_PLUS and not self.is_plan_expired()

    def can_use_ml(self):
        return self.plan in (self.PLAN_PRO, self.PLAN_PRO_PLUS) and not self.is_plan_expired()

    def can_use_crickbot(self):
        return self.plan in (self.PLAN_PRO, self.PLAN_PRO_PLUS) and not self.is_plan_expired()

    def is_plan_expired(self):
        from django.utils import timezone
        if self.plan == self.PLAN_FREE:
            return False
        if self.plan_expires_at and timezone.now() > self.plan_expires_at:
            return True
        return False

    def effective_plan(self):
        if self.is_plan_expired():
            return self.PLAN_FREE
        return self.plan