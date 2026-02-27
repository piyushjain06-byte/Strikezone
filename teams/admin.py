
# Register your models here.
from django.contrib import admin
from .models import TeamDetails, PlayerDetails, TournamentTeam, TournamentRoster


@admin.register(TeamDetails)
class TeamDetailsAdmin(admin.ModelAdmin):
    list_display = ('team_code', 'team_name', 'team_created_date')
    search_fields = ('team_name',)


@admin.register(PlayerDetails)
class PlayerDetailsAdmin(admin.ModelAdmin):
    list_display = ('player_name', 'mobile_number')
    search_fields = ('player_name', 'mobile_number')


@admin.register(TournamentTeam)
class TournamentTeamAdmin(admin.ModelAdmin):
    list_display = ('tournament', 'team', 'created_at')
    list_filter = ('tournament',)
    search_fields = ('team__team_name', 'tournament__tournament_name')


@admin.register(TournamentRoster)
class TournamentRosterAdmin(admin.ModelAdmin):
    list_display = ('tournament', 'tournament_team', 'player', 'role', 'is_captain', 'is_vice_captain', 'jersey_number')
    list_filter = ('tournament', 'role', 'is_captain', 'is_vice_captain')
    search_fields = ('player__player_name', 'player__mobile_number', 'tournament_team__team__team_name')