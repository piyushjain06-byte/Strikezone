from django.db import models
from firstcricketapp.models import UpperCategory,TournamentDetails,TeamDetails,PlayerDetails,CreateMatch,StartTournament,MatchStart
from django.core.exceptions import ValidationError


class Innings(models.Model):
    match_start = models.OneToOneField(
        "firstcricketapp.MatchStart",
        on_delete=models.CASCADE,
        related_name="innings"
    )

    # Auto-decided from toss (readonly in admin)
    batting_team = models.ForeignKey(
        "firstcricketapp.TeamDetails",
        on_delete=models.CASCADE,
        related_name="innings_batting"
    )

    bowling_team = models.ForeignKey(
        "firstcricketapp.TeamDetails",
        on_delete=models.CASCADE,
        related_name="innings_bowling"
    )

    # Default values = 0
    total_runs = models.IntegerField(default=0)
    total_wickets = models.IntegerField(default=0)
    total_overs = models.FloatField(default=0)

    # Initial selected players
    striker = models.ForeignKey(
        "firstcricketapp.PlayerDetails",
        on_delete=models.CASCADE,
        related_name="innings_striker"
    )

    non_striker = models.ForeignKey(
        "firstcricketapp.PlayerDetails",
        on_delete=models.CASCADE,
        related_name="innings_non_striker"
    )

    bowler = models.ForeignKey(
        "firstcricketapp.PlayerDetails",
        on_delete=models.CASCADE,
        related_name="innings_bowler"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Innings"

    def clean(self):

        # 🛑 Safety check (admin form loading protection)
        if not self.match_start_id:
            return

        match = self.match_start.match

        # ✅ Match must be started
        if not self.match_start.is_match_started:
            raise ValidationError("Match is not started.")

        toss_winner = self.match_start.toss_winner
        decision = self.match_start.decision

        # 🎯 Decide expected batting team
        if decision == "BAT":
            expected_batting = toss_winner
        else:
            expected_batting = (
                match.team1 if toss_winner == match.team2 else match.team2
            )

        expected_bowling = (
            match.team1 if expected_batting == match.team2 else match.team2
        )

        # 🔒 Enforce correct teams
        if self.batting_team != expected_batting:
            raise ValidationError("Batting team must follow toss decision.")

        if self.bowling_team != expected_bowling:
            raise ValidationError("Bowling team must follow toss decision.")

        # 🏏 Striker & Non-striker must belong to batting team
        if self.striker.team != self.batting_team:
            raise ValidationError("Striker must be from batting team.")

        if self.non_striker.team != self.batting_team:
            raise ValidationError("Non-striker must be from batting team.")

        if self.striker == self.non_striker:
            raise ValidationError("Striker and Non-striker cannot be same player.")

        # 🎯 Bowler must belong to bowling team
        if self.bowler.team != self.bowling_team:
            raise ValidationError("Bowler must be from bowling team.")

    def save(self, *args, **kwargs):
        """
        Auto-set batting & bowling team based on toss.
        User cannot change these manually.
        """

        if self.match_start_id:
            match = self.match_start.match
            toss_winner = self.match_start.toss_winner
            decision = self.match_start.decision

            if decision == "BAT":
                self.batting_team = toss_winner
            else:
                self.batting_team = (
                    match.team1 if toss_winner == match.team2 else match.team2
                )

            self.bowling_team = (
                match.team1 if self.batting_team == match.team2 else match.team2
            )

        super().save(*args, **kwargs)

    def __str__(self):
        tournament = self.match_start.match.tournament
        match = self.match_start.match
        return f"{tournament} - {match}"