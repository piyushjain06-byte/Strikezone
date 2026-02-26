from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import (
    UpperCategory,
    TournamentDetails,
    TeamDetails,
    PlayerDetails,
    CreateMatch,
    StartTournament,
    MatchStart,
    GuestUser
   
    

)


# ----------------------------
# Basic Model Registrations
# ----------------------------

admin.site.register(UpperCategory)
admin.site.register(TournamentDetails)
admin.site.register(TeamDetails)
admin.site.register(PlayerDetails)
admin.site.register(CreateMatch)




# ----------------------------
# Start Tournament Admin
# ----------------------------

@admin.register(StartTournament)
class StartTournamentAdmin(admin.ModelAdmin):
    list_display = ("tournament", "is_started", "started_at")

    def save_model(self, request, obj, form, change):
        # Ensure only one tournament is active at a time
        if obj.is_started:
            if StartTournament.objects.filter(is_started=True).exclude(id=obj.id).exists():
                raise ValidationError("Only one tournament can be active at a time.")
        super().save_model(request, obj, form, change)


# ----------------------------
# Match Start (Toss Control)
# ----------------------------

@admin.register(MatchStart)
class MatchStartAdmin(admin.ModelAdmin):

    list_display = (
        "match",
        "toss_winner",
        "toss_loser",
        "batting_team",
        "bowling_team",
        "decision",
        "is_match_started",
        "started_at"
    )

    # 🔒 Prevent adding if tournament not started
    def has_add_permission(self, request):
        if StartTournament.objects.filter(is_started=True).exists():
            return True
        return False

    # 👀 Show only matches of active tournament
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        started_tournament = StartTournament.objects.filter(is_started=True).first()

        if started_tournament:
            return qs.filter(match__tournament=started_tournament.tournament)

        return qs.none()

    # 🎯 Filter match dropdown to only active tournament matches
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "match":
            started_tournament = StartTournament.objects.filter(is_started=True).first()

            if started_tournament:
                kwargs["queryset"] = CreateMatch.objects.filter(
                    tournament=started_tournament.tournament
                )
            else:
                kwargs["queryset"] = CreateMatch.objects.none()

        return super().formfield_for_foreignkey(db_field, request, **kwargs)
    
from .models import Innings, Over, Ball, BattingScorecard, BowlingScorecard, MatchResult

@admin.register(Innings)
class InningsAdmin(admin.ModelAdmin):
    readonly_fields = ['batting_team', 'bowling_team', 'total_runs',
                       'total_wickets', 'total_balls', 'extras']

    def save_model(self, request, obj, form, change):
        match_start = obj.match.match_start  # get MatchStart for this match

        if obj.innings_number == 1:
            obj.batting_team = match_start.batting_team
            obj.bowling_team = match_start.bowling_team
        else:
            obj.batting_team = match_start.bowling_team  # swapped for 2nd innings
            obj.bowling_team = match_start.batting_team

        super().save_model(request, obj, form, change)
        
admin.site.register(Over)
admin.site.register(Ball)
admin.site.register(BattingScorecard)
admin.site.register(BowlingScorecard)
admin.site.register(MatchResult)
admin.site.register(GuestUser)
    
