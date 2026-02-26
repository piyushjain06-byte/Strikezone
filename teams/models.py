from django.db import models

# Create your models here.
from django.db import models
from tournaments.models import TournamentDetails


# ---------------------------------
# Team Model
# ---------------------------------
class TeamDetails(models.Model):
    team_name = models.CharField(max_length=100)

    tournament = models.ForeignKey(
        TournamentDetails,
        on_delete=models.CASCADE,
        related_name="teams"
    )

    team_created_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Team Details'

    def __str__(self):
        return self.team_name


# ---------------------------------
# Player Model
# ---------------------------------
class PlayerDetails(models.Model):

    PLAYER_ROLE = [
        ('BATSMAN', 'Batsman'),
        ('BOWLER', 'Bowler'),
        ('ALLROUNDER', 'All-Rounder'),
        ('WICKETKEEPER', 'Wicket Keeper'),
    ]

    player_name = models.CharField(max_length=100)

    team = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name="players"
    )

    role = models.CharField(
        max_length=20,
        choices=PLAYER_ROLE,
        default='BATSMAN'
    )

    is_captain = models.BooleanField(default=False)
    is_vice_captain = models.BooleanField(default=False)

    jersey_number = models.PositiveIntegerField(null=True, blank=True)

    mobile_number = models.CharField(
        max_length=15,
        unique=True,
        null=True,
        blank=True,
        help_text="Player's mobile number used for login (e.g. 9876543210)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Player Details'

    def __str__(self):
        return self.player_name