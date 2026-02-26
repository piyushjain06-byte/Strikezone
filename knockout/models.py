
# Create your models here.
from django.db import models
from tournaments.models import TournamentDetails
from teams.models import TeamDetails
from matches.models import CreateMatch


# ---------------------------------
# Knockout Stage Model
# ---------------------------------
class KnockoutStage(models.Model):

    STAGE_CHOICES = [
        ('PQF', 'Pre Quarter Final'),
        ('QF',  'Quarter Final'),
        ('SF',  'Semi Final'),
        ('F',   'Final'),
    ]

    tournament = models.ForeignKey(
        TournamentDetails,
        on_delete=models.CASCADE,
        related_name='knockout_stages'
    )

    stage = models.CharField(max_length=10, choices=STAGE_CHOICES)

    stage_order = models.PositiveIntegerField(default=1)

    is_completed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tournament', 'stage')
        ordering = ['stage_order']
        verbose_name_plural = 'Knockout Stages'

    def __str__(self):
        return f"{self.tournament.tournament_name} - {self.get_stage_display()}"


# ---------------------------------
# Knockout Match Model
# ---------------------------------
class KnockoutMatch(models.Model):

    stage = models.ForeignKey(
        KnockoutStage,
        on_delete=models.CASCADE,
        related_name='matches'
    )

    match_number = models.PositiveIntegerField()

    team1 = models.ForeignKey(
        TeamDetails,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knockout_team1_matches'
    )

    team2 = models.ForeignKey(
        TeamDetails,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knockout_team2_matches'
    )

    team1_label = models.CharField(max_length=50, blank=True)
    team2_label = models.CharField(max_length=50, blank=True)

    match = models.OneToOneField(
        CreateMatch,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knockout_match'
    )

    winner = models.ForeignKey(
        TeamDetails,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knockout_wins'
    )

    next_match = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='fed_by'
    )

    venue = models.CharField(max_length=200, blank=True)
    match_date = models.DateField(null=True, blank=True)

    is_completed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('stage', 'match_number')
        ordering = ['match_number']
        verbose_name_plural = 'Knockout Matches'

    def __str__(self):
        return f"{self.stage.get_stage_display()} Match {self.match_number} - {self.stage.tournament.tournament_name}"