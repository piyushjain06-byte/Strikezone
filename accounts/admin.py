from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import GuestUser


@admin.register(GuestUser)
class GuestUserAdmin(admin.ModelAdmin):
    list_display = ('mobile_number', 'created_at')
    search_fields = ('mobile_number',)