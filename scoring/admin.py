from django.contrib import admin
from .models import Innings, Over, Ball, BattingScorecard, BowlingScorecard


@admin.register(Innings)
class InningsAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'innings_number', 'batting_team', 'bowling_team', 'total_runs', 'total_wickets', 'overs_completed', 'status')
    list_filter = ('status', 'innings_number')


@admin.register(Over)
class OverAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'over_number', 'bowler', 'runs_in_over', 'wickets_in_over', 'is_completed')
    list_filter = ('is_completed',)


@admin.register(Ball)
class BallAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'ball_number', 'batsman', 'bowler', 'runs_off_bat', 'extra_runs', 'total_runs', 'ball_type', 'is_wicket', 'wicket_type')
    list_filter = ('ball_type', 'is_wicket', 'wicket_type')


@admin.register(BattingScorecard)
class BattingScorecardAdmin(admin.ModelAdmin):
    list_display = ('batsman', 'innings', 'runs', 'balls_faced', 'fours', 'sixes', 'strike_rate', 'status')
    list_filter = ('status',)


@admin.register(BowlingScorecard)
class BowlingScorecardAdmin(admin.ModelAdmin):
    list_display = ('bowler', 'innings', 'overs_bowled', 'runs_given', 'wickets', 'wides', 'no_balls', 'economy')