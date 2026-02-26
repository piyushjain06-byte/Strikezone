from django.db import models

# Create your models here.
from django.db import models
from django.core.exceptions import ValidationError
from tournaments.models import TournamentDetails
from teams.models import TeamDetails


# ---------------------------------
# Create Match Model
# ---------------------------------
class CreateMatch(models.Model):
    tournament = models.ForeignKey(
        TournamentDetails,
        on_delete=models.CASCADE,
        related_name='matches'
    )
    team1 = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name='team1_matches'
    )
    team2 = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name='team2_matches'
    )
    match_date = models.DateField()
    venue = models.CharField(max_length=200)

    class Meta:
        verbose_name_plural = 'Create Matches for a Tournament'

    def clean(self):
        if self.team1 == self.team2:
            raise ValidationError("Both the Teams cannot be the Same")

        if self.team1.tournament != self.tournament:
            raise ValidationError(f"The selected Team1 must be of the {self.tournament}")

        if self.team2.tournament != self.tournament:
            raise ValidationError(f"The selected Team2 must be of the {self.tournament}")

    def __str__(self):
        return f"{self.team1} vs {self.team2}"


# ---------------------------------
# Match Start Model
# ---------------------------------
class MatchStart(models.Model):
    match = models.OneToOneField(
        CreateMatch,
        on_delete=models.CASCADE,
        related_name="match_start"
    )

    toss_winner = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name="won_toss_matches"
    )

    DECISION_CHOICES = [
        ("BAT", "BAT"),
        ("BOWL", "BOWL"),
    ]

    decision = models.CharField(
        max_length=10,
        choices=DECISION_CHOICES
    )

    toss_loser = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name="lost_toss_matches",
        blank=True,
        null=True
    )

    batting_team = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name="batting_matches",
        blank=True,
        null=True
    )

    bowling_team = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name="bowling_matches",
        blank=True,
        null=True
    )

    is_match_started = models.BooleanField(default=False)
    started_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Match Start"

    def clean(self):
        # Tournament must be started
        if self.match and hasattr(self.match.tournament, "start_info"):
            if not self.match.tournament.start_info.is_started:
                raise ValidationError(
                    "Cannot start match. Tournament is not started yet."
                )

        # Toss winner must be one of the match teams
        if self.match and self.toss_winner:
            if self.toss_winner not in [self.match.team1, self.match.team2]:
                raise ValidationError(
                    "Toss winner must be one of the two teams playing."
                )

    def save(self, *args, **kwargs):
        if self.match and self.toss_winner and self.decision:
            if self.toss_winner == self.match.team1:
                self.toss_loser = self.match.team2
            elif self.toss_winner == self.match.team2:
                self.toss_loser = self.match.team1

            if self.decision == "BAT":
                self.batting_team = self.toss_winner
                self.bowling_team = self.toss_loser
            elif self.decision == "BOWL":
                self.bowling_team = self.toss_winner
                self.batting_team = self.toss_loser

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.match} - {self.toss_winner} chose to {self.decision}"


# ---------------------------------
# Match Result Model
# ---------------------------------
class MatchResult(models.Model):

    RESULT_TYPE = [
        ("WIN_BY_RUNS", "Win by Runs"),
        ("WIN_BY_WICKETS", "Win by Wickets"),
        ("TIE", "Tie"),
        ("NO_RESULT", "No Result"),
    ]

    match = models.OneToOneField(
        CreateMatch,
        on_delete=models.CASCADE,
        related_name="result"
    )

    winner = models.ForeignKey(
        TeamDetails,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="won_matches"
    )

    result_type = models.CharField(max_length=20, choices=RESULT_TYPE)

    win_margin = models.PositiveIntegerField(null=True, blank=True)

    result_summary = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.result_summary