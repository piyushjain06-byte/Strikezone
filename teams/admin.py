
# Register your models here.
from django.contrib import admin
from .models import TeamDetails, PlayerDetails


@admin.register(TeamDetails)
class TeamDetailsAdmin(admin.ModelAdmin):
    list_display = ('team_name', 'tournament', 'team_created_date')
    list_filter = ('tournament',)
    search_fields = ('team_name',)


@admin.register(PlayerDetails)
class PlayerDetailsAdmin(admin.ModelAdmin):
    list_display = ('player_name', 'team', 'role', 'is_captain', 'is_vice_captain', 'jersey_number', 'mobile_number')
    list_filter = ('role', 'is_captain', 'is_vice_captain', 'team')
    search_fields = ('player_name', 'mobile_number')