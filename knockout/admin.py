
# Register your models here.
from django.contrib import admin
from .models import KnockoutStage, KnockoutMatch


@admin.register(KnockoutStage)
class KnockoutStageAdmin(admin.ModelAdmin):
    list_display = ('tournament', 'stage', 'stage_order', 'is_completed')
    list_filter = ('stage', 'is_completed', 'tournament')


@admin.register(KnockoutMatch)
class KnockoutMatchAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'team1', 'team2', 'winner', 'venue', 'match_date', 'is_completed')
    list_filter = ('is_completed', 'stage')