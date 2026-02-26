from django.db import models

# Create your models here.



# ---------------------------------
# Guest User Model
# ---------------------------------
class GuestUser(models.Model):
    mobile_number = models.CharField(max_length=15, unique=True)
    password = models.CharField(max_length=128)  # stored as plain text (simple app)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Guest Users'

    def __str__(self):
        return f"Guest: {self.mobile_number}"