# Register your models here.
from django.contrib import admin
from .models import UpperCategory, TournamentDetails, StartTournament


@admin.register(UpperCategory)
class UpperCategoryAdmin(admin.ModelAdmin):
    list_display = ('category_name',)
    search_fields = ('category_name',)


@admin.register(TournamentDetails)
class TournamentDetailsAdmin(admin.ModelAdmin):
    list_display = ('tournament_name', 'tournament_type', 'start_date', 'end_date', 'number_of_teams', 'number_of_overs')
    list_filter = ('tournament_type',)
    search_fields = ('tournament_name',)


@admin.register(StartTournament)
class StartTournamentAdmin(admin.ModelAdmin):
    list_display = ('tournament', 'is_started', 'started_at')
    list_filter = ('is_started',)