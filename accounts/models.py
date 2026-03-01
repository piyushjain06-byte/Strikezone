from django.db import models
from django.contrib.auth.hashers import check_password, identify_hasher, make_password

# Create your models here.



# ---------------------------------
# Guest User Model
# ---------------------------------
class GuestUser(models.Model):
    mobile_number = models.CharField(max_length=15, unique=True)
    # Stores a Django password hash (PBKDF2 by default). Older rows may still contain
    # a legacy plain-text value; login code upgrades those on successful auth.
    password = models.CharField(max_length=128)
    is_mobile_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Guest Users'

    def __str__(self):
        return f"Guest: {self.mobile_number}"

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