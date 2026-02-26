

# Register your models here.
from django.contrib import admin
from .models import CreateMatch, MatchStart, MatchResult


@admin.register(CreateMatch)
class CreateMatchAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'tournament', 'match_date', 'venue')
    list_filter = ('tournament',)
    search_fields = ('venue',)


@admin.register(MatchStart)
class MatchStartAdmin(admin.ModelAdmin):
    list_display = ('match', 'toss_winner', 'decision', 'batting_team', 'bowling_team', 'is_match_started')
    list_filter = ('decision', 'is_match_started')


@admin.register(MatchResult)
class MatchResultAdmin(admin.ModelAdmin):
    list_display = ('match', 'winner', 'result_type', 'win_margin', 'result_summary')
    list_filter = ('result_type',)